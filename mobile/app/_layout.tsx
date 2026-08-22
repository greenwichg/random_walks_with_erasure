import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import { useColorScheme } from "react-native";

import { initApi } from "@/lib/api";
import { loadSession } from "@/lib/session";
import { dark, light } from "@/design/tokens";

/**
 * The app shell.
 *
 * Two things happen before anything renders, and the order matters:
 *
 *   1. `initApi()` points the shared client at the deployment and at the keystore reader. At module
 *      scope, so it runs once per bundle load and before any hook can fire a request — the same
 *      placement `components/providers.tsx` uses on the web, for the same reason.
 *   2. `loadSession()` reads the token out of the keystore into the synchronous cache the client's
 *      `getToken` reads. It is async, so the first frame waits on it: rendering before it resolves
 *      would send one unauthenticated request and show a signed-out screen to a signed-in reader.
 */
initApi();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      // A phone loses the network constantly — in a lift, on a train. Retrying twice turns most of
      // that into a slightly slow load instead of an error state.
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

export default function RootLayout() {
  const scheme = useColorScheme();
  const palette = scheme === "dark" ? dark : light;
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Never rejects — `loadSession` turns an unreadable keystore into "signed out" rather than a
    // crash, so there is no failure branch to handle here.
    void loadSession().finally(() => setReady(true));
  }, []);

  if (!ready) return null;

  return (
    <QueryClientProvider client={queryClient}>
      <StatusBar style={scheme === "dark" ? "light" : "dark"} />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: palette.background },
          headerTintColor: palette.foreground,
          headerTitleStyle: { color: palette.foreground },
          contentStyle: { backgroundColor: palette.background },
        }}
      >
        <Stack.Screen name="index" options={{ title: "Hidden View" }} />
        <Stack.Screen name="sign-in" options={{ title: "Sign in", presentation: "modal" }} />
      </Stack>
    </QueryClientProvider>
  );
}
