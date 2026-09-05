import * as React from "react";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { FeedbackEffectGroup, NotificationChannelPrefs, RecFeedbackType, Settings } from "@ih/core/domain/types";
import { countryName } from "@ih/core/logic/countries";
import { diffSettings, hasChanges } from "@ih/core/logic/settings-diff";

import { PageTitle, Screen } from "@/components/layout/screen";
import { TAB_BAR_HEIGHT } from "@/components/layout/tab-bar";
import { CountryBadge } from "@/components/shared/country-badge";
import { CountryPicker } from "@/components/shared/country-picker";
import { SectionCard } from "@/components/shared/section-card";
import { ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Icon, type IconName } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { useFeedbackEffects, usePlaceCountries, usePushConfig, useRemoveFeedback, useSettings, useUpdateSettings } from "@/lib/hooks";
import { openOnWeb } from "@/lib/navigation";
import { useTheme, type ThemePreference } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "pt", label: "Português" },
];

function opennessLabelKey(v: number) {
  if (v < 25) return "settings.openness.close";
  if (v < 55) return "settings.openness.gentle";
  if (v < 80) return "settings.openness.regular";
  return "settings.openness.push";
}
function strengthLabelKey(v: number) {
  if (v < 25) return "settings.strength.subtle";
  if (v < 55) return "settings.strength.balanced";
  if (v < 80) return "settings.strength.assertive";
  return "settings.strength.bold";
}

const INTEREST_DEFAULT = 5;
const INTERESTS: { key: keyof Settings["interests"]; icon: IconName; labelKey: string }[] = [
  { key: "business", icon: "briefcase", labelKey: "settings.interest.business" },
  { key: "technology", icon: "cpu", labelKey: "settings.interest.technology" },
  { key: "science", icon: "flask", labelKey: "settings.interest.science" },
  { key: "health", icon: "heart-pulse", labelKey: "settings.interest.health" },
  { key: "climate", icon: "leaf", labelKey: "settings.interest.climate" },
  { key: "sports", icon: "trophy", labelKey: "settings.interest.sports" },
  { key: "entertainment", icon: "clapperboard", labelKey: "settings.interest.entertainment" },
  { key: "artsCulture", icon: "palette", labelKey: "settings.interest.artsCulture" },
];
const DEFAULT_INTERESTS: Settings["interests"] = {
  business: INTEREST_DEFAULT,
  technology: INTEREST_DEFAULT,
  science: INTEREST_DEFAULT,
  health: INTEREST_DEFAULT,
  climate: INTEREST_DEFAULT,
  sports: INTEREST_DEFAULT,
  entertainment: INTEREST_DEFAULT,
  artsCulture: INTEREST_DEFAULT,
};

const FEEDBACK_TYPE_LABEL: Record<RecFeedbackType, string> = {
  like: "rec.like",
  dislike: "rec.dislike",
  ignore: "rec.ignore",
  read_later: "rec.readLater",
  another_viewpoint: "settings.fb.anotherViewpoint",
  already_know: "settings.fb.alreadyKnow",
  too_repetitive: "settings.fb.tooRepetitive",
  fewer_from_source: "settings.fb.fewerFromSource",
  more_topic: "settings.fb.moreTopic",
};

/**
 * Settings — the mobile web's page, section for section: Appearance · Recommendations · For You
 * country · Recommendation feedback · Interests · Places · Reports · Notifications · Privacy, with
 * the floating save bar. `base` is the server snapshot the draft was seeded from; `draft` the
 * reader's working copy; the Save button sends only `diffSettings(base, draft)` — theme excluded,
 * because it has its own instant write-through.
 *
 * Absent from the web page, and why (docs/MOBILE_APP_PLAN.md §4): the browser-extension token
 * card (`/api/me/tokens` is session-only by design) and the per-device Web Push toggle.
 */
export default function SettingsScreen() {
  const { data, isLoading, isError, refetch } = useSettings();
  const updateSettings = useUpdateSettings();
  const persistTheme = useUpdateSettings();
  const { palette, preference, setPreference } = useTheme();
  const { t, lang } = useTranslation();
  const insets = useSafeAreaInsets();
  const pushConfig = usePushConfig();

  const { data: feedbackFx } = useFeedbackEffects();
  const removeFeedback = useRemoveFeedback();
  const [showDismissed, setShowDismissed] = React.useState(false);
  const removeEffectGroup = (g: FeedbackEffectGroup) =>
    g.signals.forEach((s) => removeFeedback.mutate({ articleId: s.articleId, feedback: s.feedback }));

  const [base, setBase] = React.useState<Settings | null>(null);
  const [draft, setDraft] = React.useState<Settings | null>(null);
  const countries = usePlaceCountries();
  const recCountryRanked = React.useMemo(
    () => [...(countries.data ?? [])].sort((a, b) => b.articles - a.articles || a.country.localeCompare(b.country)),
    [countries.data],
  );
  const recCountryTop = React.useMemo(() => recCountryRanked.filter((c) => c.articles > 0).slice(0, 12), [recCountryRanked]);
  const countriesPending = countries.isLoading || countries.isFetching;
  const countryListState = (n: number) =>
    countriesPending && n === 0 ? (
      <View style={styles.chips} accessibilityElementsHidden>
        {[64, 88, 72, 80, 68].map((w, i) => (
          <Skeleton key={i} height={30} width={w} style={{ borderRadius: radius.pill }} />
        ))}
      </View>
    ) : n === 0 ? (
      <Txt size={12} muted>
        {countries.isError ? t("settings.countriesUnavailable") : t("settings.countriesEmpty")}
      </Txt>
    ) : null;

  const [saved, setSaved] = React.useState(false);

  const patch = React.useMemo<Partial<Settings>>(() => {
    if (!base || !draft) return {};
    const p = diffSettings(base, draft);
    delete p.theme;
    return p;
  }, [base, draft]);
  const dirty = hasChanges(patch);

  React.useEffect(() => {
    if (!data) return;
    if (!base || !draft || !dirty) {
      setBase(data);
      setDraft(data);
    }
  }, [data, base, draft, dirty]);

  // Restore the account's saved theme ONCE per mount, if this device currently shows something
  // different — the cross-device apply, as on the web.
  const appliedStoredTheme = React.useRef(false);
  React.useEffect(() => {
    if (appliedStoredTheme.current || !data?.theme) return;
    appliedStoredTheme.current = true;
    if (data.theme !== preference) setPreference(data.theme);
  }, [data?.theme, preference, setPreference]);

  function applyTheme(value: ThemePreference) {
    if (value === preference) return;
    setPreference(value);
    persistTheme.mutate({ theme: value });
  }

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
    setSaved(false);
  }
  function setNotif<K extends keyof Settings["notifications"]>(key: K, value: boolean) {
    setDraft((d) => (d ? { ...d, notifications: { ...d.notifications, [key]: value } } : d));
    setSaved(false);
  }
  function setDigestChannel(channel: "inApp" | "push" | "email", value: boolean) {
    setDraft((d) =>
      d
        ? {
            ...d,
            notifications: {
              ...d.notifications,
              categories: { ...d.notifications.categories, digests: { ...d.notifications.categories?.digests, [channel]: value } },
            },
          }
        : d,
    );
    setSaved(false);
  }
  function setInterest(key: keyof Settings["interests"], value: number) {
    setDraft((d) => (d ? { ...d, interests: { ...d.interests, [key]: value } } : d));
    setSaved(false);
  }
  function resetInterests() {
    setDraft((d) => (d ? { ...d, interests: { ...DEFAULT_INTERESTS } } : d));
    setSaved(false);
  }
  function setCategory(category: keyof Settings["notifications"]["categories"], channel: keyof NotificationChannelPrefs, value: boolean) {
    setDraft((d) =>
      d
        ? {
            ...d,
            notifications: {
              ...d.notifications,
              categories: { ...d.notifications.categories, [category]: { ...d.notifications.categories[category], [channel]: value } },
            },
          }
        : d,
    );
    setSaved(false);
  }

  function save() {
    if (!hasChanges(patch)) return;
    updateSettings.mutate(patch, {
      onSuccess: (persisted) => {
        setBase(persisted);
        setDraft(persisted);
        setSaved(true);
      },
    });
  }
  function reset() {
    if (base) setDraft(base);
    setSaved(false);
  }

  const chip = (on: boolean) => [
    styles.chip,
    on ? { borderColor: alpha(palette.primary, 0.4), backgroundColor: alpha(palette.primary, 0.1) } : { borderColor: palette.border },
  ];
  const chipInk = (on: boolean) => (on ? palette.primary : palette.mutedForeground);
  const showBar = dirty || saved || updateSettings.isPending || updateSettings.isError;

  return (
    <View style={{ flex: 1 }}>
      <Screen contentStyle={{ paddingBottom: showBar ? 120 : 24 }}>
        <PageTitle title={t("settings.title")} subtitle={t("settings.subtitle")} />

        {isLoading && (
          <View style={{ gap: 24 }} accessibilityElementsHidden>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} height={224} />
            ))}
          </View>
        )}
        {isError && <ErrorState onRetry={() => void refetch()} />}

        {draft && (
          <View style={{ gap: 24 }}>
            {/* Appearance */}
            <SectionCard title={t("settings.appearance")} info={t("settings.appearanceInfo")}>
              <SettingRow title={t("settings.theme")} description={t("settings.themeDesc")}>
                <View style={[styles.segmented, { borderColor: palette.border, backgroundColor: palette.muted }]}>
                  {(
                    [
                      { value: "light", labelKey: "settings.theme.light", icon: "sun" },
                      { value: "dark", labelKey: "settings.theme.dark", icon: "moon" },
                      { value: "system", labelKey: "settings.theme.system", icon: "monitor" },
                    ] as const
                  ).map((o) => {
                    const on = preference === o.value;
                    return (
                      <Pressable
                        key={o.value}
                        accessibilityRole="button"
                        accessibilityState={{ selected: on }}
                        onPress={() => applyTheme(o.value)}
                        style={[styles.segment, on && { backgroundColor: palette.card }]}
                      >
                        <Icon name={o.icon} size={16} color={on ? palette.foreground : palette.mutedForeground} />
                        <Txt size={14} weight="500" color={on ? palette.foreground : palette.mutedForeground}>
                          {t(o.labelKey)}
                        </Txt>
                      </Pressable>
                    );
                  })}
                </View>
              </SettingRow>
              <SettingRow title={t("settings.language")} description={t("settings.languageDesc")} last>
                <View style={styles.chips}>
                  {LANGUAGES.map((l) => {
                    const on = draft.language === l.value;
                    return (
                      <Pressable key={l.value} accessibilityRole="button" accessibilityState={{ selected: on }} onPress={() => set("language", l.value)} style={chip(on)}>
                        <Txt size={14} weight="500" color={chipInk(on)}>
                          {l.label}
                        </Txt>
                      </Pressable>
                    );
                  })}
                </View>
              </SettingRow>
            </SectionCard>

            {/* Recommendations */}
            <SectionCard title={t("settings.recommendations")} info={t("settings.recommendationsInfo")}>
              <View style={{ gap: 32 }}>
                <SliderRow
                  icon="scale"
                  title={t("settings.politicalOpenness")}
                  description={t("settings.opennessDesc")}
                  value={draft.politicalOpenness}
                  onChange={(v) => set("politicalOpenness", v)}
                  valueLabel={t(opennessLabelKey(draft.politicalOpenness))}
                />
                <SliderRow
                  icon="sparkles"
                  title={t("settings.recommendationStrength")}
                  description={t("settings.strengthDesc")}
                  value={draft.recommendationStrength}
                  onChange={(v) => set("recommendationStrength", v)}
                  valueLabel={t(strengthLabelKey(draft.recommendationStrength))}
                />
                <SliderRow
                  icon="target"
                  title={t("settings.readingGoal")}
                  description={t("settings.readingGoalFull")}
                  value={draft.readingGoalMinutes}
                  onChange={(v) => set("readingGoalMinutes", v)}
                  min={5}
                  max={120}
                  step={5}
                  valueLabel={t("settings.perDay", { n: draft.readingGoalMinutes })}
                />
              </View>
            </SectionCard>

            {/* For You country */}
            <SectionCard
              title={t("settings.recCountry")}
              info={t("settings.recCountryInfo")}
              action={
                <Button variant="ghost" size="sm" icon="rotate-ccw" textColor={palette.mutedForeground} disabled={(draft.recommendationCountry ?? null) == null} onPress={() => set("recommendationCountry", null)}>
                  {t("settings.recCountryReset")}
                </Button>
              }
            >
              <Txt size={12} muted style={{ marginBottom: 12 }}>
                {t("settings.recCountryHint")}
              </Txt>
              {countryListState(recCountryTop.length)}
              <View style={styles.chips}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ selected: (draft.recommendationCountry ?? null) == null }}
                  onPress={() => set("recommendationCountry", null)}
                  style={chip((draft.recommendationCountry ?? null) == null)}
                >
                  <Txt size={12} weight="500" color={chipInk((draft.recommendationCountry ?? null) == null)}>
                    {t("settings.recCountryGlobal")}
                  </Txt>
                </Pressable>
                {(draft.recommendationCountry && !recCountryTop.some((c) => c.country === draft.recommendationCountry)
                  ? [{ country: draft.recommendationCountry }, ...recCountryTop]
                  : recCountryTop
                ).map((c) => {
                  const on = draft.recommendationCountry === c.country;
                  return (
                    <Pressable
                      key={c.country}
                      accessibilityRole="button"
                      accessibilityState={{ selected: on }}
                      onPress={() => set("recommendationCountry", on ? null : c.country)}
                      style={chip(on)}
                    >
                      <CountryBadge code={c.country} color={chipInk(on)} />
                    </Pressable>
                  );
                })}
                {recCountryRanked.length > recCountryTop.length && (
                  <CountryPicker
                    options={recCountryRanked}
                    isSelected={(code) => draft.recommendationCountry === code}
                    onToggle={(code) => set("recommendationCountry", draft.recommendationCountry === code ? null : code)}
                    triggerLabel={t("settings.recCountryShowAll", { n: recCountryRanked.length })}
                    searchPlaceholder={t("settings.recCountrySearch")}
                    noMatchLabel={(q) => t("settings.recCountryNoMatch", { q })}
                    dialogLabel={t("settings.recCountry")}
                  />
                )}
              </View>
            </SectionCard>

            {/* Recommendation-feedback effects — the visible half of the feedback loop. */}
            {feedbackFx && feedbackFx.publishers.length + feedbackFx.topics.length + feedbackFx.articles.length > 0 && (
              <SectionCard title={t("settings.feedback")} info={t("settings.feedbackInfo")}>
                <Txt size={12} muted style={{ marginBottom: 16 }}>
                  {t("settings.feedbackHint")}
                </Txt>
                <View style={{ gap: 20 }}>
                  {feedbackFx.publishers.length > 0 && (
                    <View>
                      <Txt size={12} weight="500" uppercase tracking={0.5} muted style={{ marginBottom: 8 }}>
                        {t("settings.fx.lessFrom")}
                      </Txt>
                      <View style={styles.chips}>
                        {feedbackFx.publishers.map((g) => (
                          <EffectChip key={g.name} label={g.name} count={g.signals.length} removeLabel={t("settings.fx.removeEffect", { name: g.name })} onRemove={() => removeEffectGroup(g)} />
                        ))}
                      </View>
                    </View>
                  )}
                  {feedbackFx.topics.length > 0 && (
                    <View>
                      <Txt size={12} weight="500" uppercase tracking={0.5} muted style={{ marginBottom: 8 }}>
                        {t("settings.fx.topics")}
                      </Txt>
                      <View style={styles.chips}>
                        {feedbackFx.topics.map((g) => (
                          <EffectChip
                            key={`${g.direction}:${g.name}`}
                            label={g.name}
                            direction={t(g.direction === "more" ? "settings.fx.more" : "settings.fx.less")}
                            count={g.signals.length}
                            removeLabel={t("settings.fx.removeEffect", { name: g.name })}
                            onRemove={() => removeEffectGroup(g)}
                          />
                        ))}
                      </View>
                    </View>
                  )}
                  {feedbackFx.articles.length > 0 && (
                    <View>
                      <Txt size={12} weight="500" uppercase tracking={0.5} muted style={{ marginBottom: 8 }}>
                        {t("settings.fx.dismissed")}
                      </Txt>
                      <View style={styles.dismissedRow}>
                        <Txt size={14} muted style={{ flex: 1, minWidth: 160 }}>
                          {t("settings.fx.dismissedSummary", { n: feedbackFx.articles.length })}
                        </Txt>
                        <Button variant="ghost" size="sm" onPress={() => setShowDismissed((v) => !v)}>
                          {showDismissed ? t("settings.fx.hideList") : t("settings.fx.showList")}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          textColor={palette.mutedForeground}
                          onPress={() => feedbackFx.articles.forEach((a) => removeFeedback.mutate({ articleId: a.articleId, feedback: a.feedback }))}
                        >
                          {t("settings.fx.clearAll")}
                        </Button>
                      </View>
                      {showDismissed && (
                        <View style={{ marginTop: 4 }}>
                          {feedbackFx.articles.map((a, i) => (
                            <View key={`${a.articleId}:${a.feedback}`} style={[styles.dismissedItem, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border }]}>
                              <View style={[styles.typePill, { borderColor: palette.border }]}>
                                <Txt size={12} weight="500" lineHeight={16}>
                                  {t(FEEDBACK_TYPE_LABEL[a.feedback] ?? a.feedback)}
                                </Txt>
                              </View>
                              <View style={{ flex: 1, minWidth: 0 }}>
                                {a.headline ? (
                                  <Txt size={14} numberOfLines={1}>{a.headline}</Txt>
                                ) : a.inCatalog ? (
                                  <Txt size={12} muted numberOfLines={1}>{a.url ?? a.articleId}</Txt>
                                ) : (
                                  <Txt size={14} muted style={{ fontStyle: "italic" }}>{t("settings.fx.expired")}</Txt>
                                )}
                                {(a.publisher || fmtDate(a.createdAt, lang)) && (
                                  <Txt size={12} muted numberOfLines={1}>
                                    {[a.publisher, fmtDate(a.createdAt, lang)].filter(Boolean).join(" · ")}
                                  </Txt>
                                )}
                              </View>
                              <Button variant="ghost" size="sm" textColor={palette.mutedForeground} onPress={() => removeFeedback.mutate({ articleId: a.articleId, feedback: a.feedback })}>
                                {t("settings.feedbackRemove")}
                              </Button>
                            </View>
                          ))}
                        </View>
                      )}
                    </View>
                  )}
                </View>
              </SectionCard>
            )}

            {/* Interest Intensity */}
            <SectionCard
              title={t("settings.interests")}
              info={t("settings.interestsInfo")}
              action={
                <Button
                  variant="ghost"
                  size="sm"
                  icon="rotate-ccw"
                  textColor={palette.mutedForeground}
                  disabled={INTERESTS.every((it) => draft.interests[it.key] === INTEREST_DEFAULT)}
                  onPress={resetInterests}
                >
                  {t("settings.interestsReset")}
                </Button>
              }
            >
              <Txt size={12} muted style={{ marginBottom: 20 }}>
                {t("settings.interestsHint")}
              </Txt>
              <View style={{ gap: 20 }}>
                {INTERESTS.map((it) => (
                  <InterestSliderRow key={it.key} icon={it.icon} label={t(it.labelKey)} value={draft.interests[it.key]} onChange={(v) => setInterest(it.key, v)} />
                ))}
              </View>
            </SectionCard>

            {/* Places */}
            <SectionCard title={t("settings.places")} info={t("settings.placesInfo")}>
              <View style={{ gap: 24 }}>
                <View>
                  <Txt size={14} weight="500" style={{ marginBottom: 4 }}>
                    {t("settings.edition")}
                  </Txt>
                  <Txt size={12} muted style={{ marginBottom: 8 }}>
                    {t("settings.editionDesc")}
                  </Txt>
                  {countryListState((countries.data ?? []).length)}
                  <View style={styles.chips}>
                    <Pressable accessibilityRole="button" accessibilityState={{ selected: (draft.edition ?? null) == null }} onPress={() => set("edition", null)} style={chip((draft.edition ?? null) == null)}>
                      <Txt size={12} weight="500" color={chipInk((draft.edition ?? null) == null)}>
                        {t("settings.editionGlobal")}
                      </Txt>
                    </Pressable>
                    {draft.edition && (
                      <Pressable accessibilityRole="button" accessibilityState={{ selected: true }} onPress={() => set("edition", null)} style={chip(true)}>
                        <CountryBadge code={draft.edition} color={palette.primary} />
                      </Pressable>
                    )}
                    <CountryPicker
                      options={countries.data ?? []}
                      isSelected={(code) => draft.edition === code}
                      onToggle={(code) => set("edition", draft.edition === code ? null : code)}
                      triggerLabel={t("settings.editionChoose")}
                      searchPlaceholder={t("settings.recCountrySearch")}
                      noMatchLabel={(q) => t("settings.recCountryNoMatch", { q })}
                      dialogLabel={t("settings.edition")}
                    />
                  </View>
                </View>

                <View>
                  <Txt size={14} weight="500" style={{ marginBottom: 4 }}>
                    {t("settings.followedPlaces")}
                  </Txt>
                  <Txt size={12} muted style={{ marginBottom: 8 }}>
                    {t("settings.followedPlacesDesc")}
                  </Txt>
                  {(() => {
                    const list = draft.locations ?? [];
                    const followed = list.filter((l) => l.level === "country");
                    const atCap = followed.length >= 10;
                    const toggle = (code: string) => {
                      const on = list.some((l) => l.placeId === code && l.level === "country");
                      if (!on && atCap) return;
                      set(
                        "locations",
                        on ? list.filter((l) => !(l.placeId === code && l.level === "country")) : [...list, { placeId: code, level: "country" as const }],
                      );
                    };
                    return (
                      <View style={styles.chips}>
                        {followed.map((l) => (
                          <Pressable
                            key={l.placeId}
                            accessibilityRole="button"
                            accessibilityLabel={`${t("common.clear")}: ${countryName(l.placeId, lang)}`}
                            onPress={() => toggle(l.placeId)}
                            style={[chip(true), { flexDirection: "row", alignItems: "center", gap: 4 }]}
                          >
                            <CountryBadge code={l.placeId} color={palette.primary} />
                            <Icon name="x" size={12} color={palette.primary} style={{ opacity: 0.6 }} />
                          </Pressable>
                        ))}
                        <CountryPicker
                          options={countries.data ?? []}
                          isSelected={(code) => list.some((l) => l.placeId === code && l.level === "country")}
                          onToggle={toggle}
                          multi
                          full={atCap}
                          fullNote={t("settings.followedPlacesDesc")}
                          triggerLabel={t("settings.followedPlacesAdd", { n: followed.length, max: 10 })}
                          searchPlaceholder={t("settings.recCountrySearch")}
                          noMatchLabel={(q) => t("settings.recCountryNoMatch", { q })}
                          dialogLabel={t("settings.followedPlaces")}
                        />
                      </View>
                    );
                  })()}
                </View>
              </View>
            </SectionCard>

            {/* Reports */}
            <SectionCard title={t("settings.reports")} info={t("settings.reportsInfo")}>
              <ToggleRow icon="file-text" title={t("settings.monthlyReport")} description={t("settings.monthlyReportDesc")} checked={draft.monthlyReport} onChange={(v) => set("monthlyReport", v)} last />
            </SectionCard>

            {/* Notifications */}
            <SectionCard title={t("settings.notifications")} info={t("settings.notificationsInfo")}>
              <ToggleRow icon="bell" title={t("settings.notif.recs")} description={t("settings.notif.recsDesc")} checked={draft.notifications.recommendations} onChange={(v) => setNotif("recommendations", v)} first />
              <ToggleRow icon="bell" title={t("settings.notif.digest")} description={t("settings.notif.digestDesc")} checked={draft.notifications.weeklyDigest} onChange={(v) => setNotif("weeklyDigest", v)} />
              {draft.notifications.weeklyDigest && (
                <View style={{ paddingLeft: 36 }}>
                  <ToggleRow
                    icon="mail"
                    title={t("settings.notif.digestEmail")}
                    description={t("settings.notif.digestEmailDesc")}
                    checked={draft.notifications.categories?.digests?.email ?? false}
                    onChange={(v) => setDigestChannel("email", v)}
                  />
                </View>
              )}
              <ToggleRow icon="bell" title={t("settings.notif.streak")} description={t("settings.notif.streakDesc")} checked={draft.notifications.streakReminders} onChange={(v) => setNotif("streakReminders", v)} />
              <ToggleRow icon="bell" title={t("settings.notif.blindSpot")} description={t("settings.notif.blindSpotDesc")} checked={draft.notifications.blindSpotAlerts} onChange={(v) => setNotif("blindSpotAlerts", v)} />
              <ToggleRow
                icon="zap"
                title={t("settings.notif.breaking")}
                description={t("settings.notif.breakingDesc")}
                checked={draft.notifications.categories.breaking.inApp}
                onChange={(v) => setCategory("breaking", "inApp", v)}
                last={!pushConfig.data?.enabled}
              />
              {pushConfig.data?.enabled && (
                <ToggleRow
                  icon="bell-ring"
                  title={t("settings.notif.breakingPush")}
                  description={t("settings.notif.breakingPushDesc")}
                  checked={draft.notifications.categories.breaking.push}
                  onChange={(v) => setCategory("breaking", "push", v)}
                  last
                />
              )}
            </SectionCard>

            {/* Privacy & data */}
            <SectionCard title={t("settings.privacy")} info={t("settings.privacyInfo")}>
              <Pressable accessibilityRole="link" onPress={() => openOnWeb("/privacy")} style={({ pressed }) => [styles.privacyRow, pressed && { backgroundColor: palette.accent }]}>
                <View style={[styles.iconBox, { backgroundColor: palette.muted }]}>
                  <Icon name="shield-check" size={16} color={palette.mutedForeground} />
                </View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Txt size={14} weight="500">
                    {t("settings.privacy.policy")}
                  </Txt>
                  <Txt size={14} muted style={{ marginTop: 2 }}>
                    {t("settings.privacy.policyDesc")}
                  </Txt>
                </View>
                <Icon name="external-link" size={16} color={palette.mutedForeground} />
              </Pressable>
            </SectionCard>
          </View>
        )}
      </Screen>

      {/* Floating save bar — the whole save lifecycle: saving, failed (+ Retry), saved, unsaved. */}
      {showBar && (
        <View style={[styles.saveBarWrap, { bottom: TAB_BAR_HEIGHT + insets.bottom + 16 }]} pointerEvents="box-none">
          <View accessibilityLiveRegion="polite" style={[styles.saveBar, { backgroundColor: alpha(palette.card, 0.95), borderColor: palette.border }]}>
            {updateSettings.isPending ? (
              <View style={styles.saveStatus}>
                <ActivityIndicator size="small" color={palette.mutedForeground} />
                <Txt size={14} muted>
                  {t("settings.saving")}
                </Txt>
              </View>
            ) : updateSettings.isError ? (
              <View style={styles.saveStatus}>
                <Icon name="alert-circle" size={16} color={palette.negative} />
                <Txt size={14} weight="500" color={palette.negative}>
                  {t("settings.saveFailed")}
                </Txt>
              </View>
            ) : saved ? (
              <View style={styles.saveStatus}>
                <Icon name="check" size={16} color={palette.positive} />
                <Txt size={14} weight="500" color={palette.positive}>
                  {t("common.allChangesSaved")}
                </Txt>
              </View>
            ) : (
              <View style={styles.saveStatus}>
                <View style={[styles.dot, { backgroundColor: palette.caution }]} />
                <Txt size={14} muted>
                  {t("settings.unsaved")}
                </Txt>
              </View>
            )}
            <View style={styles.saveActions}>
              {updateSettings.isError ? (
                <Button size="sm" onPress={save} style={{ borderRadius: radius.pill }}>
                  {t("settings.retry")}
                </Button>
              ) : saved ? null : (
                <>
                  <Button variant="ghost" size="sm" icon="rotate-ccw" textColor={palette.mutedForeground} disabled={updateSettings.isPending} onPress={reset}>
                    {t("common.reset")}
                  </Button>
                  <Button size="sm" disabled={updateSettings.isPending} onPress={save} style={{ borderRadius: radius.pill }}>
                    {t("settings.saveChanges")}
                  </Button>
                </>
              )}
            </View>
          </View>
        </View>
      )}
    </View>
  );
}

function fmtDate(iso: string, lang: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : new Intl.DateTimeFormat(lang, { dateStyle: "medium" }).format(d);
}

/* ------------------------------------------------------------------ *
 * Row primitives
 * ------------------------------------------------------------------ */
function SettingRow({ title, description, children, last = false }: { title: string; description?: string; children: React.ReactNode; last?: boolean }) {
  const { palette } = useTheme();
  return (
    <View style={[styles.settingRow, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: palette.border, paddingBottom: 16 }]}>
      <View>
        <Txt size={14} weight="500">
          {title}
        </Txt>
        {description && (
          <Txt size={14} muted style={{ marginTop: 2 }}>
            {description}
          </Txt>
        )}
      </View>
      <View>{children}</View>
    </View>
  );
}

function ToggleRow({
  icon,
  title,
  description,
  checked,
  onChange,
  first = false,
  last = false,
}: {
  icon: IconName;
  title: string;
  description?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  first?: boolean;
  last?: boolean;
}) {
  const { palette } = useTheme();
  return (
    <View style={[styles.toggleRow, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border }, last && { paddingBottom: 0 }]}>
      <View style={styles.toggleHead}>
        <View style={styles.toggleTitle}>
          <View style={[styles.iconBox, { backgroundColor: palette.muted }]}>
            <Icon name={icon} size={16} color={palette.mutedForeground} />
          </View>
          <Txt size={14} weight="500" style={{ flex: 1, minWidth: 0 }}>
            {title}
          </Txt>
        </View>
        <Switch checked={checked} onChange={onChange} accessibilityLabel={title} />
      </View>
      {description && (
        <Txt size={12} muted lineHeight={18} style={{ marginTop: 6, paddingLeft: 44 }}>
          {description}
        </Txt>
      )}
    </View>
  );
}

function EffectChip({ label, direction, count, removeLabel, onRemove }: { label: string; direction?: string; count: number; removeLabel: string; onRemove: () => void }) {
  const { palette } = useTheme();
  return (
    <View style={[styles.effectChip, { borderColor: palette.border }]}>
      {direction && (
        <Txt size={12} weight="500" muted>
          {direction}
        </Txt>
      )}
      <Txt size={12} weight="500">
        {label}
      </Txt>
      {count > 1 && <Txt size={12} muted>{`×${count}`}</Txt>}
      <Pressable accessibilityRole="button" accessibilityLabel={removeLabel} hitSlop={6} onPress={onRemove} style={styles.effectRemove}>
        <Icon name="x" size={12} color={palette.mutedForeground} />
      </Pressable>
    </View>
  );
}

function InterestSliderRow({ icon, label, value, onChange }: { icon: IconName; label: string; value: number; onChange: (v: number) => void }) {
  const { palette } = useTheme();
  return (
    <View style={styles.interestRow}>
      <View style={[styles.iconBox, { backgroundColor: palette.muted }]}>
        <Icon name={icon} size={16} color={palette.mutedForeground} />
      </View>
      <Txt size={14} weight="500" numberOfLines={1} style={{ width: 96 }}>
        {label}
      </Txt>
      <Slider value={value} min={1} max={10} step={1} onChange={onChange} accessibilityLabel={label} style={{ flex: 1, minWidth: 0 }} />
      <Txt size={14} weight="500" tabular color={palette.primary} align="right" style={{ width: 24 }}>
        {value}
      </Txt>
    </View>
  );
}

function SliderRow({
  icon,
  title,
  description,
  value,
  onChange,
  valueLabel,
  min = 0,
  max = 100,
  step = 1,
}: {
  icon: IconName;
  title: string;
  description?: string;
  value: number;
  onChange: (v: number) => void;
  valueLabel: string;
  min?: number;
  max?: number;
  step?: number;
}) {
  const { palette } = useTheme();
  return (
    <View>
      <View style={{ marginBottom: 12 }}>
        <View style={styles.toggleHead}>
          <View style={styles.toggleTitle}>
            <View style={[styles.iconBox, { backgroundColor: palette.muted }]}>
              <Icon name={icon} size={16} color={palette.mutedForeground} />
            </View>
            <Txt size={14} weight="500" style={{ flex: 1, minWidth: 0 }}>
              {title}
            </Txt>
          </View>
          <View style={[styles.valuePill, { backgroundColor: alpha(palette.primary, 0.1) }]}>
            <Txt size={12} weight="500" color={palette.primary} lineHeight={16}>
              {valueLabel}
            </Txt>
          </View>
        </View>
        {description && (
          <Txt size={12} muted lineHeight={18} style={{ marginTop: 6, paddingLeft: 44 }}>
            {description}
          </Txt>
        )}
      </View>
      <Slider value={value} min={min} max={max} step={step} onChange={onChange} accessibilityLabel={title} style={{ marginLeft: 44 }} />
    </View>
  );
}

const styles = StyleSheet.create({
  settingRow: { gap: 12, marginBottom: 16 },
  segmented: { flexDirection: "row", alignSelf: "flex-start", borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.lg, padding: 4 },
  segment: { flexDirection: "row", alignItems: "center", gap: 6, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 6 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  dismissedRow: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 },
  dismissedItem: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 8 },
  typePill: { borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 },
  toggleRow: { paddingVertical: 16 },
  toggleHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 16 },
  toggleTitle: { flexDirection: "row", alignItems: "center", gap: 12, flex: 1, minWidth: 0 },
  iconBox: { width: 32, height: 32, borderRadius: radius.lg, alignItems: "center", justifyContent: "center" },
  valuePill: { borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 4 },
  effectChip: { flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 1, borderRadius: radius.pill, paddingLeft: 10, paddingRight: 4, paddingVertical: 2 },
  effectRemove: { padding: 2 },
  interestRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  privacyRow: { flexDirection: "row", alignItems: "center", gap: 16, marginHorizontal: -8, paddingHorizontal: 8, paddingVertical: 12, borderRadius: radius.lg },
  saveBarWrap: { position: "absolute", left: 16, right: 16, alignItems: "center" },
  saveBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    width: "100%",
    maxWidth: 448,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.pill,
    paddingHorizontal: 12,
    paddingVertical: 8,
    shadowColor: "#000",
    shadowOpacity: 0.12,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  saveStatus: { flexDirection: "row", alignItems: "center", gap: 8, paddingLeft: 8, flexShrink: 1 },
  saveActions: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 8, height: 8, borderRadius: radius.pill },
});
