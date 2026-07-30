// engine-identity tests (node --test, type-stripped like the other lib tests).
//
// `upsertEngineUser` is the web tier's only way to turn a third-party identity into an engine user id,
// and it is called on the sign-in path where a wrong request means a session that can never be
// attributed. These pin the request it sends and the four ways it can decline to return an id. The
// helper moved out of lib/auth.ts unchanged; this is the first test coverage it has had.
import { test } from "node:test";
import assert from "node:assert/strict";

import { upsertEngineUser } from "./engine-identity.ts";

interface Call {
  url: string;
  init: RequestInit;
}

/** Run `fn` with `fetch` replaced, restoring the real one (and RWE_INTERNAL_SECRET) afterwards. */
async function withFetch(
  reply: () => unknown,
  fn: (calls: Call[]) => Promise<void>,
  secret?: string,
): Promise<void> {
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realSecret = process.env.RWE_INTERNAL_SECRET;
  const calls: Call[] = [];

  g.fetch = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const value = reply();
    if (value instanceof Error) throw value;
    return value;
  };
  if (secret === undefined) delete process.env.RWE_INTERNAL_SECRET;
  else process.env.RWE_INTERNAL_SECRET = secret;

  try {
    await fn(calls);
  } finally {
    g.fetch = realFetch;
    if (realSecret === undefined) delete process.env.RWE_INTERNAL_SECRET;
    else process.env.RWE_INTERNAL_SECRET = realSecret;
  }
}

const ok = (body: unknown) => ({ ok: true, json: async () => body });
const notOk = (status: number) => ({ ok: false, status, json: async () => ({}) });

const IDENTITY = {
  provider: "google",
  providerAccountId: "108461123456789012345",
  email: "reader@example.com",
  displayName: "A Reader",
};

test("posts the identity to the engine, keyed on providerAccountId", async () => {
  await withFetch(() => ok({ userId: 42 }), async (calls) => {
    assert.equal(await upsertEngineUser(IDENTITY), 42);
    assert.equal(calls.length, 1);

    const { url, init } = calls[0]!;
    assert.ok(url.endsWith("/api/internal/users"), url);
    assert.equal(init.method, "POST");
    assert.equal(init.cache, "no-store");
    assert.equal((init.headers as Record<string, string>)["Content-Type"], "application/json");

    // The engine joins identities on (provider, provider_account_id) and never on email — see
    // docs/IDENTITY_UPSERT_CONCURRENCY.md I5. The body must carry the account id as its own field.
    assert.deepEqual(JSON.parse(init.body as string), {
      provider: "google",
      providerAccountId: "108461123456789012345",
      email: "reader@example.com",
      displayName: "A Reader",
    });
  });
});

test("null / absent profile fields are omitted from the body, not sent as null", async () => {
  // The engine treats a supplied value as "refresh this" and a missing one as "leave it alone", so
  // sending null would blank a returning reader's profile.
  await withFetch(() => ok({ userId: 7 }), async (calls) => {
    await upsertEngineUser({ provider: "dev", providerAccountId: "d-1", email: null });
    assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
      provider: "dev",
      providerAccountId: "d-1",
    });
  });
});

test("sends X-IH-Auth when the internal secret is configured", async () => {
  await withFetch(() => ok({ userId: 1 }), async (calls) => {
    await upsertEngineUser(IDENTITY);
    assert.equal((calls[0]!.init.headers as Record<string, string>)["X-IH-Auth"], "s3cret");
  }, "s3cret");
});

test("omits X-IH-Auth when no secret is configured (open dev engine)", async () => {
  await withFetch(() => ok({ userId: 1 }), async (calls) => {
    await upsertEngineUser(IDENTITY);
    assert.equal("X-IH-Auth" in (calls[0]!.init.headers as Record<string, string>), false);
  });
});

test("a non-2xx response resolves to null rather than throwing", async () => {
  // 401 is the shape a missing/incorrect internal secret takes in production.
  for (const status of [401, 403, 500, 503]) {
    await withFetch(() => notOk(status), async () => {
      assert.equal(await upsertEngineUser(IDENTITY), null, `status ${status}`);
    });
  }
});

test("a transport failure resolves to null rather than throwing", async () => {
  await withFetch(() => new Error("ECONNREFUSED"), async () => {
    assert.equal(await upsertEngineUser(IDENTITY), null);
  });
});

test("a 2xx without a numeric userId resolves to null", async () => {
  for (const body of [{}, { userId: "42" }, { userId: null }, null]) {
    await withFetch(() => ok(body), async () => {
      assert.equal(await upsertEngineUser(IDENTITY), null, JSON.stringify(body));
    });
  }
});
