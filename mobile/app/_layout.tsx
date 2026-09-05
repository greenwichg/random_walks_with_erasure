import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import * as React from "react";
import { Platform, UIManager, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AppHeader } from "@/components/layout/header";
import { TabBar } from "@/components/layout/tab-bar";
import { FONT_FILES } from "@/design/fonts";
import { initApi } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { LanguageProvider } from "@/lib/i18n-context";
import { ThemeProvider, useTheme } from "@/lib/theme";

/**
 * The app shell.
 *
 * `initApi()` points the shared client at the deployment and at the keystore reader — at module
 * scope, so it runs once per bundle load and before any hook can fire a request. The providers
 * then stack in dependency order: the query client (everything reads through it), the theme, the
 * keystore session (which clears the query cache on sign-out), and the language (which reads the
 * settings query). Nothing renders until the fonts and the session have both loaded.
 *
 * ONE navigator. The web's every page sits inside one shell — sticky masthead, fixed bottom tab
 * bar — so the native stack renders the same header on every screen and the tab bar is drawn once
 * over the whole stack, not inside a tabs navigator that would leave the story page without it.
 * Routes are the web's paths, one for one.
 *
 * The sign-in gate is the web's middleware: signed out, the only reachable screen is sign-in.
 */
initApi();

if (Platform.OS === "android" && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

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
  const [fontsLoaded] = useFonts(FONT_FILES);
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <AuthProvider>
            <LanguageProvider>{fontsLoaded ? <Shell /> : null}</LanguageProvider>
          </AuthProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}

function Shell() {
  const { palette, scheme } = useTheme();
  const { ready, signedIn } = useAuth();
  if (!ready) return <View style={{ flex: 1, backgroundColor: palette.background }} />;

  return (
    <View style={{ flex: 1, backgroundColor: palette.background }}>
      <StatusBar style={scheme === "dark" ? "light" : "dark"} />
      <Stack
        screenOptions={{
          header: () => <AppHeader />,
          contentStyle: { backgroundColor: palette.background },
          animation: "slide_from_right",
        }}
      >
        <Stack.Protected guard={signedIn}>
          <Stack.Screen name="index" />
          <Stack.Screen name="recommendations" />
          <Stack.Screen name="search" />
          <Stack.Screen name="stories/index" />
          <Stack.Screen name="stories/[id]" />
          <Stack.Screen name="publishers/[name]" />
          <Stack.Screen name="settings" />
          <Stack.Screen name="alerts" />
          <Stack.Screen name="saved" />
          <Stack.Screen name="menu" options={{ headerShown: false, presentation: "fullScreenModal", animation: "slide_from_bottom" }} />
        </Stack.Protected>
        <Stack.Protected guard={!signedIn}>
          <Stack.Screen name="sign-in" options={{ headerShown: false }} />
        </Stack.Protected>
      </Stack>
      {signedIn && <TabBar />}
    </View>
  );
}
