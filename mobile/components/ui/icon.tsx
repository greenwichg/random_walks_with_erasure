import * as React from "react";
import { Feather, Ionicons, MaterialCommunityIcons } from "@expo/vector-icons";

import { useTheme } from "@/lib/theme";

/**
 * The icon set, named after the lucide icons the web uses.
 *
 * lucide-react is DOM-only (SVG elements), and `lucide-react-native` would add a second copy of
 * every glyph to the bundle for the sake of a name. Feather is lucide's own ancestor — the same
 * strokes at the same weight — and ships with Expo; the handful of glyphs Feather lacks come from
 * the two other bundled sets. Callers keep the web's vocabulary (`<Icon name="eye-off" />`) so a
 * component reads the same on both platforms.
 */
type Glyph =
  | { set: "feather"; name: React.ComponentProps<typeof Feather>["name"] }
  | { set: "mci"; name: React.ComponentProps<typeof MaterialCommunityIcons>["name"] }
  | { set: "ion"; name: React.ComponentProps<typeof Ionicons>["name"] };

const f = (name: React.ComponentProps<typeof Feather>["name"]): Glyph => ({ set: "feather", name });
const m = (name: React.ComponentProps<typeof MaterialCommunityIcons>["name"]): Glyph => ({ set: "mci", name });
const i = (name: React.ComponentProps<typeof Ionicons>["name"]): Glyph => ({ set: "ion", name });

const GLYPHS = {
  menu: f("menu"),
  x: f("x"),
  search: f("search"),
  bell: f("bell"),
  "bell-ring": m("bell-ring-outline"),
  sun: f("sun"),
  moon: f("moon"),
  monitor: f("monitor"),
  "chevron-right": f("chevron-right"),
  "chevron-down": f("chevron-down"),
  "chevron-left": f("chevron-left"),
  "chevron-up": f("chevron-up"),
  "arrow-left": f("arrow-left"),
  "arrow-right": f("arrow-right"),
  "arrow-left-right": m("swap-horizontal"),
  "eye-off": f("eye-off"),
  eye: f("eye"),
  newspaper: m("newspaper-variant-outline"),
  users: f("users"),
  "user-plus": f("user-plus"),
  sparkles: i("sparkles-outline"),
  "map-pin": f("map-pin"),
  puzzle: m("puzzle-outline"),
  "scan-search": m("text-search"),
  "trending-up": f("trending-up"),
  "trending-down": f("trending-down"),
  minus: f("minus"),
  flag: f("flag"),
  scale: m("scale-balance"),
  milestone: m("sign-direction"),
  clock: f("clock"),
  gauge: m("gauge"),
  activity: f("activity"),
  flame: m("fire"),
  radio: f("radio"),
  snowflake: m("snowflake"),
  archive: f("archive"),
  quote: m("format-quote-open"),
  info: f("info"),
  check: f("check"),
  "check-check": m("check-all"),
  plus: f("plus"),
  share: f("share-2"),
  "more-vertical": f("more-vertical"),
  "more-horizontal": f("more-horizontal"),
  refresh: f("refresh-cw"),
  "alert-circle": f("alert-circle"),
  inbox: f("inbox"),
  bookmark: f("bookmark"),
  "bookmark-check": m("bookmark-check-outline"),
  "external-link": f("external-link"),
  "book-open": f("book-open"),
  "file-text": f("file-text"),
  building: m("office-building-outline"),
  sliders: f("sliders"),
  route: m("routes"),
  compass: f("compass"),
  wand: m("auto-fix"),
  "thumbs-up": f("thumbs-up"),
  "thumbs-down": f("thumbs-down"),
  "help-circle": f("help-circle"),
  "corner-down-left": f("corner-down-left"),
  mail: f("mail"),
  zap: f("zap"),
  briefcase: f("briefcase"),
  cpu: f("cpu"),
  flask: m("flask-outline"),
  "heart-pulse": m("heart-pulse"),
  leaf: m("leaf"),
  trophy: m("trophy-outline"),
  clapperboard: m("movie-open-outline"),
  palette: m("palette-outline"),
  "rotate-ccw": f("rotate-ccw"),
  "shield-check": m("shield-check-outline"),
  calendar: f("calendar"),
  "bar-chart": f("bar-chart-2"),
  repeat: f("repeat"),
  "minus-circle": f("minus-circle"),
  "plus-circle": f("plus-circle"),
  target: f("target"),
  "log-out": f("log-out"),
  globe: f("globe"),
  loader: f("loader"),
} as const;

export type IconName = keyof typeof GLYPHS;

export function Icon({
  name,
  size = 16,
  color,
  style,
}: {
  name: IconName;
  size?: number;
  color?: string;
  style?: React.ComponentProps<typeof Feather>["style"];
}) {
  const { palette } = useTheme();
  const glyph = GLYPHS[name];
  const c = color ?? palette.foreground;
  if (glyph.set === "mci") return <MaterialCommunityIcons name={glyph.name} size={size} color={c} style={style} />;
  if (glyph.set === "ion") return <Ionicons name={glyph.name} size={size} color={c} style={style} />;
  return <Feather name={glyph.name} size={size} color={c} style={style} />;
}
