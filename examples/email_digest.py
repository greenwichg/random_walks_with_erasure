"""The weekly digest email: subject, plain text, and HTML, in the reader's language.

**Why the copy lives here and not in `web/messages`.** The web catalog is the UI's, and the api
image does not contain `web/` at all (see deploy/Dockerfile.api) — so rendering from it would mean
either copying the catalog into the image or having the engine call the web tier mid-send. Neither
is worth it, because email copy is *different* copy: a subject line, a preheader, a footer naming
the sender, and unsubscribe wording that has to stand on its own outside the app. Those strings do
not exist in the UI catalog and would be invented there only to serve this file.

The five languages match `settings_service._SETTINGS_LANGUAGES`, and an unknown language falls back
to English rather than to a key name — a reader who set a language we do not have should get a
readable email, not `notifications.weekly_digest.title`.

**The numbers are lifted, never recomputed.** Every figure comes from the notification payload the
in-app digest already carries, so the email and the inbox card cannot disagree about the same week.
This module composes; it does not measure.
"""

from __future__ import annotations

import html as _html

#: Localized email copy. `{}` placeholders are filled by `render`; every language carries the same
#: keys, and `_STRINGS["en"]` is the fallback for anything missing.
_STRINGS: dict = {
    "en": {
        "subject": "Your reading week: {reads} articles",
        "subject_none": "Your reading week",
        "preheader": "A round-up of your reading week.",
        "greeting": "Here's your week in reading.",
        "reads": "Articles read",
        "streak": "Day streak",
        "overall": "Information Health",
        "cta": "Open your report",
        "why": "You're receiving this because weekly digests are on for your account.",
        "unsubscribe": "Unsubscribe from weekly digests",
        "settings": "Manage notification settings",
    },
    "es": {
        "subject": "Tu semana de lectura: {reads} artículos",
        "subject_none": "Tu semana de lectura",
        "preheader": "Un resumen de tu semana de lectura.",
        "greeting": "Esta es tu semana de lectura.",
        "reads": "Artículos leídos",
        "streak": "Días seguidos",
        "overall": "Salud informativa",
        "cta": "Abrir tu informe",
        "why": "Recibes esto porque los resúmenes semanales están activados en tu cuenta.",
        "unsubscribe": "Darse de baja de los resúmenes semanales",
        "settings": "Gestionar las notificaciones",
    },
    "fr": {
        "subject": "Votre semaine de lecture : {reads} articles",
        "subject_none": "Votre semaine de lecture",
        "preheader": "Un récapitulatif de votre semaine de lecture.",
        "greeting": "Voici votre semaine de lecture.",
        "reads": "Articles lus",
        "streak": "Jours d'affilée",
        "overall": "Santé informationnelle",
        "cta": "Ouvrir votre rapport",
        "why": "Vous recevez ceci parce que les résumés hebdomadaires sont activés sur votre compte.",
        "unsubscribe": "Se désabonner des résumés hebdomadaires",
        "settings": "Gérer les notifications",
    },
    "de": {
        "subject": "Ihre Lesewoche: {reads} Artikel",
        "subject_none": "Ihre Lesewoche",
        "preheader": "Ein Rückblick auf Ihre Lesewoche.",
        "greeting": "Hier ist Ihre Lesewoche.",
        "reads": "Gelesene Artikel",
        "streak": "Tage in Folge",
        "overall": "Informationsgesundheit",
        "cta": "Bericht öffnen",
        "why": "Sie erhalten dies, weil wöchentliche Zusammenfassungen für Ihr Konto aktiviert sind.",
        "unsubscribe": "Wöchentliche Zusammenfassungen abbestellen",
        "settings": "Benachrichtigungen verwalten",
    },
    "pt": {
        "subject": "A sua semana de leitura: {reads} artigos",
        "subject_none": "A sua semana de leitura",
        "preheader": "Um resumo da sua semana de leitura.",
        "greeting": "Esta é a sua semana de leitura.",
        "reads": "Artigos lidos",
        "streak": "Dias seguidos",
        "overall": "Saúde informativa",
        "cta": "Abrir o seu relatório",
        "why": "Está a receber isto porque os resumos semanais estão ativados na sua conta.",
        "unsubscribe": "Cancelar a subscrição dos resumos semanais",
        "settings": "Gerir notificações",
    },
}


def strings(lang: str) -> dict:
    """The catalog for a language, English-backed so a missing key can never reach a reader."""
    base = dict(_STRINGS["en"])
    base.update(_STRINGS.get((lang or "en").split("-")[0].lower(), {}))
    return base


def _n(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def render(payload: dict, *, lang: str = "en", base_url: str = "",
           unsubscribe: str = "", settings_url: str = "") -> dict:
    """``{subject, text, html}`` for one reader's week.

    ``payload`` is the weekly-digest notification's own payload — ``reads``, ``streakDays``,
    ``overall`` — so the mail states exactly what the in-app card states.

    Every interpolated value is HTML-escaped on the way in. These are integers today, but a
    template that only happens to be safe becomes an injection the first time someone adds a topic
    name to the payload."""
    s = strings(lang)
    reads = _n((payload or {}).get("reads"))
    streak = _n((payload or {}).get("streakDays"))
    overall = (payload or {}).get("overall")
    report_url = f"{base_url.rstrip('/')}/report" if base_url else ""

    subject = s["subject"].format(reads=reads) if reads else s["subject_none"]

    rows = [(s["reads"], str(reads)), (s["streak"], str(streak))]
    if overall is not None:
        rows.append((s["overall"], f"{_n(overall)}/100"))

    text_lines = [s["greeting"], ""]
    text_lines += [f"{label}: {value}" for label, value in rows]
    if report_url:
        text_lines += ["", f"{s['cta']}: {report_url}"]
    text_lines += ["", s["why"]]
    if unsubscribe:
        text_lines.append(f"{s['unsubscribe']}: {unsubscribe}")
    if settings_url:
        text_lines.append(f"{s['settings']}: {settings_url}")
    text = "\n".join(text_lines) + "\n"

    e = _html.escape
    row_html = "".join(
        f'<tr><td style="padding:6px 0;color:#5b5b66;font-size:14px">{e(label)}</td>'
        f'<td style="padding:6px 0;text-align:right;font-weight:600;font-size:14px">{e(value)}</td></tr>'
        for label, value in rows)
    cta_html = (f'<p style="margin:24px 0"><a href="{e(report_url)}" '
                f'style="background:#6d5cf5;color:#fff;text-decoration:none;padding:10px 18px;'
                f'border-radius:8px;font-size:14px;display:inline-block">{e(s["cta"])}</a></p>'
                if report_url else "")
    foot_links = " · ".join(
        f'<a href="{e(url)}" style="color:#5b5b66">{e(label)}</a>'
        for label, url in ((s["unsubscribe"], unsubscribe), (s["settings"], settings_url)) if url)

    html_doc = f"""<!doctype html>
<html lang="{e(lang or 'en')}"><body style="margin:0;background:#f6f6f8;font-family:-apple-system,Segoe UI,Roboto,sans-serif">
<span style="display:none;font-size:1px;color:#f6f6f8">{e(s['preheader'])}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f6f8;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#fff;border-radius:12px;padding:28px">
<tr><td>
<h1 style="margin:0 0 18px;font-size:18px;color:#16161a">{e(s['greeting'])}</h1>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{row_html}</table>
{cta_html}
<p style="margin:20px 0 6px;color:#8a8a94;font-size:12px;line-height:1.5">{e(s['why'])}</p>
<p style="margin:0;font-size:12px">{foot_links}</p>
</td></tr></table>
</td></tr></table>
</body></html>"""

    return {"subject": subject, "text": text, "html": html_doc}
