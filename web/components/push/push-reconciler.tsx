"use client";

import * as React from "react";
import { pushSupported, registerServiceWorker, repairSubscription } from "@/lib/push-client";
import { usePushConfig } from "@/hooks/use-push";

/**
 * Keep this device's push subscription and the engine's record of it in agreement — everywhere in
 * the app, not only where the toggle happens to be rendered.
 *
 * Mounted once in the authenticated shell; renders nothing.
 *
 * **Why this is not left to `usePush`.** It was, and that was the bug. `usePush` is consumed by
 * exactly one component — the settings toggle — so reconciliation only ever ran on `/settings`. Both
 * desynchronisations it repairs happen while the reader is somewhere else entirely:
 *
 * * a **VAPID rotation** invalidates every subscription at the moment the operator restarts the api;
 * * a **`410 Gone`** prunes the engine's row during a fan-out the reader never sees, which is
 *   ordinary attrition rather than an error — browsers revoke subscriptions on profile changes, long
 *   idle periods, and site-data clears.
 *
 * In both cases the device goes dark silently, and the reader has no reason to visit Settings: from
 * everything they can see, push is on. Recovery that waits for them to open the one page that
 * happens to host the toggle is recovery that mostly does not happen.
 *
 * **Authenticated shell, not the root providers.** `engineKnowsEndpoint` reads the signed-in
 * reader's own subscriptions, so mounting this above the sign-in boundary would fire a user-scoped
 * request for every anonymous visitor to the landing page and get nothing back but a 401.
 *
 * Registering the worker app-wide is the second thing this buys, and it is not incidental: a service
 * worker that is not registered never receives `pushsubscriptionchange`, so the browser's own
 * rotation of an endpoint could not be reported either.
 *
 * Prompts for nothing, ever. `shouldRepairSubscription` refuses without granted permission and an
 * existing subscription, so this can only restore what the reader already chose — never choose for
 * them.
 */
export function PushReconciler() {
  const config = usePushConfig();

  React.useEffect(() => {
    if (!pushSupported() || !config.enabled || !config.publicKey) return;
    void (async () => {
      // REGISTER FIRST, and await it. `repairSubscription` reads the held subscription through
      // `getRegistration()`, which answers null while no worker is registered — so a repair run
      // before registration concludes there is nothing to repair and returns silently. That is the
      // wrong answer on exactly the devices that need it most: a cleared profile, a fresh browser.
      await registerServiceWorker();
      // Fire-and-forget from here: this never throws, and nothing on the page awaits the answer. The
      // settings toggle awaits the same call through `singleFlight`, which is what stops the two
      // triggers from racing into two subscriptions on the one page that mounts both.
      await repairSubscription(config.publicKey, config.enabled);
    })();
  }, [config.enabled, config.publicKey]);

  return null;
}
