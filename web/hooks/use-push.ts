"use client";

/**
 * `usePush` — the reader-facing state of browser push on THIS device (B1).
 *
 * Four independent signals decide what the UI may offer, and none of them can be inferred from
 * another: whether the browser has the APIs, whether the server has push configured and switched on,
 * what the browser's notification permission currently is, and whether this device holds a
 * subscription against the key the server serves now. `pushUiState` collapses them; this hook keeps
 * them fresh.
 *
 * Permission changes are OBSERVED, not assumed: a reader can grant or revoke notifications from the
 * browser's own UI at any moment, with the page open and no interaction with ours. Without the
 * `permissions` listener the toggle would keep describing a state that stopped being true.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { queryKeys, services } from "@ih/core/api/services";
import {
  currentPermission,
  currentSubscription,
  pushSupported,
  registerServiceWorker,
  repairSubscription,
  subscribe as subscribeDevice,
  subscriptionIsCurrent,
  unsubscribe as unsubscribeDevice,
} from "@/lib/push-client";
import { pushUiState, type PushPermission, type PushUiState } from "@/lib/push";

interface PushConfig {
  enabled: boolean;
  publicKey: string;
}

const NO_PUSH: PushConfig = { enabled: false, publicKey: "" };

/**
 * Whether this DEPLOYMENT offers push, and the key to subscribe against.
 *
 * Split out of {@link usePush} because the two questions have different scopes and different
 * consumers. This one is about the server and nothing else — no browser APIs are touched, no worker
 * is registered, no subscription is read — which is what lets the **account-level** category
 * preference ask it. That preference travels with the reader to every device they own, so gating it
 * on whether *this* browser happens to support push would hide a setting that is not about this
 * browser at all.
 *
 * Fails closed: an unreachable engine reports push unavailable, the same posture the proxy route
 * behind it takes.
 */
export function usePushConfig(): PushConfig {
  // React-query rather than a per-mount fetch (R1b): three consumers mount app-wide (the
  // reconciler, the settings toggle, the account-level preference), and each used to ask the
  // server again — the RUM waterfalls showed /api/push/config two and three times per page. One
  // cached answer serves them all, and ShellPrefetch seeds it from the bootstrap payload so the
  // common path never fetches here at all. Fails closed exactly as before: no answer is NO_PUSH.
  const { data } = useQuery({
    queryKey: queryKeys.pushConfig,
    queryFn: services.pushConfig,
    staleTime: 300_000,          // deployment-level fact; five minutes is generous, not risky
  });
  return data ? { enabled: !!data.enabled, publicKey: data.publicKey ?? "" } : NO_PUSH;
}

export interface UsePush {
  state: PushUiState;
  permission: PushPermission;
  /** A request is in flight (the permission prompt is open, or we are registering). */
  busy: boolean;
  /** The last attempt failed for a reason that is not "blocked" — shown as an inline error. */
  failed: boolean;
  enable: () => Promise<void>;
  disable: () => Promise<void>;
}

export function usePush(): UsePush {
  const config = usePushConfig();
  const [permission, setPermission] = React.useState<PushPermission>("unsupported");
  const [subscribed, setSubscribed] = React.useState(false);
  // Tracked separately from `subscribed` because the two answer different questions, and during a
  // rollback only this one can be answered: `subscribed` needs a server key to compare against, and a
  // switched-off deployment serves none — so without this a still-registered device would look like a
  // device that was never registered, and the reader would lose the only control that removes it.
  const [hasSubscription, setHasSubscription] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [failed, setFailed] = React.useState(false);

  const refresh = React.useCallback(async (key: string) => {
    setPermission(currentPermission());
    setHasSubscription((await currentSubscription()) !== null);
    setSubscribed(key ? await subscriptionIsCurrent(key) : false);
  }, []);

  // Register the worker as soon as push is known to be available — before the reader asks for
  // anything. Registration prompts for nothing; it only means the worker is ready to be subscribed
  // against, and it is also what makes `pushsubscriptionchange` reach us on a device that already
  // granted permission on a previous visit.
  //
  // Then reconcile. `PushReconciler` in the app shell triggers the same repair on every page, so this
  // is no longer the only caller and is no longer what makes repair happen — but it stays, because
  // this hook must AWAIT the repair before `refresh` reads the subscription it renders. Dropping it
  // would let the toggle paint from state the reconciler was still changing. `singleFlight` collapses
  // the two triggers into one run, so mounting both costs one repair, not two.
  React.useEffect(() => {
    if (!pushSupported()) return;
    let alive = true;
    (async () => {
      // Only register and repair when the server offers push. When it does not, still refresh: a
      // device registered before a rollback must be discoverable, or the reader cannot remove it.
      if (config.enabled) {
        await registerServiceWorker();
        await repairSubscription(config.publicKey, config.enabled);
      }
      if (alive) await refresh(config.publicKey);
    })();
    return () => {
      alive = false;
    };
  }, [config.enabled, config.publicKey, refresh]);

  // Observe permission changes made outside our UI (browser settings, the site-information panel).
  // `navigator.permissions` is absent on older Safari, hence the guard — the state simply stays as
  // last read there, which is the same behaviour this hook had before the listener existed.
  React.useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.permissions?.query) return;
    let status: PermissionStatus | null = null;
    const onChange = () => void refresh(config.publicKey);
    (async () => {
      try {
        status = await navigator.permissions.query({ name: "notifications" as PermissionName });
        status.addEventListener("change", onChange);
      } catch {
        /* unsupported query name — nothing to observe */
      }
    })();
    return () => status?.removeEventListener("change", onChange);
  }, [config.publicKey, refresh]);

  const enable = React.useCallback(async () => {
    setBusy(true);
    setFailed(false);
    try {
      const result = await subscribeDevice(config.publicKey);
      setFailed(result === "failed");
    } finally {
      await refresh(config.publicKey);
      setBusy(false);
    }
  }, [config.publicKey, refresh]);

  const disable = React.useCallback(async () => {
    setBusy(true);
    setFailed(false);
    try {
      await unsubscribeDevice();
    } finally {
      await refresh(config.publicKey);
      setBusy(false);
    }
  }, [config.publicKey, refresh]);

  return {
    state: pushUiState({
      supported: pushSupported(),
      configured: config.enabled && !!config.publicKey,
      permission,
      subscribed,
      hasSubscription,
    }),
    permission,
    busy,
    failed,
    enable,
    disable,
  };
}
