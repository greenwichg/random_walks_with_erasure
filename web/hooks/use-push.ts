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
import {
  currentPermission,
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
  const [config, setConfig] = React.useState<PushConfig>({ enabled: false, publicKey: "" });
  const [permission, setPermission] = React.useState<PushPermission>("unsupported");
  const [subscribed, setSubscribed] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [failed, setFailed] = React.useState(false);

  // Ask the server whether push is available at all before touching any browser API. A reader on a
  // deployment with no VAPID key must never see the control, let alone a permission prompt.
  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/push/config");
        if (!res.ok) return;
        const cfg = (await res.json()) as PushConfig;
        if (alive) setConfig({ enabled: !!cfg.enabled, publicKey: cfg.publicKey ?? "" });
      } catch {
        /* leave push unavailable */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const refresh = React.useCallback(async (key: string) => {
    setPermission(currentPermission());
    setSubscribed(key ? await subscriptionIsCurrent(key) : false);
  }, []);

  // Register the worker as soon as push is known to be available — before the reader asks for
  // anything. Registration prompts for nothing; it only means the worker is ready to be subscribed
  // against, and it is also what makes `pushsubscriptionchange` reach us on a device that already
  // granted permission on a previous visit.
  //
  // Then repair a subscription the server's current VAPID key can no longer be used with. This is the
  // automatic half of a key rotation: it prompts for nothing, does nothing unless this device holds a
  // subscription bound to a retired key, and without it a rotated deployment leaves every device dark
  // until each reader notices a toggle that switched itself off.
  React.useEffect(() => {
    if (!config.enabled || !pushSupported()) return;
    let alive = true;
    (async () => {
      await registerServiceWorker();
      await repairSubscription(config.publicKey, config.enabled);
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
    }),
    permission,
    busy,
    failed,
    enable,
    disable,
  };
}
