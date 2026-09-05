/**
 * The two typefaces, as the web sets them (`web/app/globals.css`, TYPE SYSTEM):
 *
 *   display  Schibsted Grotesk — every h1–h3 and the few headline moments that opt in
 *   sans     Instrument Sans — running text, labels, controls and numbers
 *
 * The web loads variable WOFF2 files from `@fontsource-variable`. A native app cannot: WOFF2 is a
 * browser format, and a variable font registered through `expo-font` exposes one face whose weight
 * axis neither platform lets a style select reliably. So the app ships STATIC instances — one file
 * per weight — and picks the FAMILY for a weight rather than setting `fontWeight` on a family, which
 * is the one combination that renders identically on Android and iOS.
 *
 * The files are Google Fonts' own static builds of the same two families (`mobile/assets/fonts`),
 * both under the SIL Open Font License.
 */

export const FONT_FILES = {
  "InstrumentSans-Regular": require("../assets/fonts/InstrumentSans-Regular.ttf"),
  "InstrumentSans-Medium": require("../assets/fonts/InstrumentSans-Medium.ttf"),
  "InstrumentSans-SemiBold": require("../assets/fonts/InstrumentSans-SemiBold.ttf"),
  "InstrumentSans-Bold": require("../assets/fonts/InstrumentSans-Bold.ttf"),
  "SchibstedGrotesk-SemiBold": require("../assets/fonts/SchibstedGrotesk-SemiBold.ttf"),
  "SchibstedGrotesk-Bold": require("../assets/fonts/SchibstedGrotesk-Bold.ttf"),
  "SchibstedGrotesk-ExtraBold": require("../assets/fonts/SchibstedGrotesk-ExtraBold.ttf"),
};

export type Weight = "400" | "500" | "600" | "700" | "800";

const SANS: Record<Weight, string> = {
  "400": "InstrumentSans-Regular",
  "500": "InstrumentSans-Medium",
  "600": "InstrumentSans-SemiBold",
  "700": "InstrumentSans-Bold",
  // Instrument Sans stops at 700; the web falls back the same way.
  "800": "InstrumentSans-Bold",
};

const DISPLAY: Record<Weight, string> = {
  // The display face is only ever set at headline weights; lighter requests take its lightest cut.
  "400": "SchibstedGrotesk-SemiBold",
  "500": "SchibstedGrotesk-SemiBold",
  "600": "SchibstedGrotesk-SemiBold",
  "700": "SchibstedGrotesk-Bold",
  "800": "SchibstedGrotesk-ExtraBold",
};

/** The family name that renders `weight` in the given face. */
export function fontFamily(weight: Weight = "400", face: "sans" | "display" = "sans"): string {
  return face === "display" ? DISPLAY[weight] : SANS[weight];
}
