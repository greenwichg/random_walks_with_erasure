# The header menu that ate your scroll position

**Symptom.** Scroll down anywhere in the app, click the avatar, and the page jumps to the top. The
scroll position is not restored when the menu closes — it is gone. While the menu is open the whole
header, avatar included, is missing from the accessibility tree.

**Status.** Fixed for the profile menu and the notifications panel. **Still present for the filter
dropdowns** on Discover, History, Search and Stories — deliberately, see §4.

---

## 1. What actually happens

Measured in Chromium against the production build, at `scrollY 381`:

| | `scrollY` | header inside `aria-hidden="true"` |
|---|---|---|
| before opening | 381 | no |
| menu open | **0** | **yes** |
| after closing | **0** | no |

Both halves of the report — "disappears" and "becomes inaccessible" — are in that table. The first
is the page throwing the reader back to the top; the second is literal, the control leaves the
accessibility tree.

## 2. Why

A Radix `DropdownMenu` is **modal by default**. Modal engages `react-remove-scroll`, which injects:

```css
body[data-scroll-locked] { overflow: hidden !important; padding-right: …px !important; }
```

Setting `overflow: hidden` on `<body>` is normally harmless, because **body's overflow propagates to
the viewport** — the viewport stops scrolling and body itself is left `visible`.

That propagation has a condition: it only happens when the **root** element's overflow is `visible`
in *both* axes. And `app/globals.css` sets:

```css
html { overflow-x: clip; }   /* MB1 H1 — guarantee zero horizontal scrolling */
```

So propagation is off, the declaration lands on `<body>` itself, and the document's scroll offset is
discarded rather than preserved.

**This is why the bug is invisible in a minimal reproduction.** Two earlier attempts to reproduce it
headlessly measured zero movement and concluded the mechanism was unconfirmed. They were sound; a
bare page simply has no `overflow-x: clip` on the root, and without that one declaration the failure
cannot occur.

## 3. The fix

`modal={false}` on `components/layout/header.tsx` (profile) and
`components/layout/notifications-menu.tsx` (bell). Neither is a modal: they are navigation. Non-modal
skips `react-remove-scroll` entirely, so nothing touches `<body>` and nothing is `aria-hidden`.

Regression tests are in `e2e/specs/header.spec.ts` — scroll position survives, header stays out of
`aria-hidden`, menu stays anchored while scrolling, works on a phone viewport, and no scroll lock is
left behind after close/reopen. Reverting the fix fails six of them.

## 4. What is deliberately NOT fixed

`components/shared/filter-select.tsx` keeps the modal default and keeps this bug.

Making non-modal the default in `components/ui/dropdown-menu.tsx` was the obvious global fix, and it
was tried. It **breaks the Stories filter reset**: `stories-filter-state.spec.ts` → "resetting a
filter cleans the parameter out of the URL" passes with `modal={true}` and fails with
`modal={false}`, because without the modal layer the menu dismisses when the router navigation from
the previous pick lands, so "pick Left, then pick All" cannot complete.

Fixing the filters therefore needs its own change — most likely controlling dismissal explicitly
rather than flipping modality — and its own verification. It was out of scope for the reported bug
and is recorded here rather than left as a surprise.

## 5. Do not "fix" it in the CSS

The tempting move is to drop `overflow-x: clip` from `html` and leave it on `body`, restoring
propagation. **Measured: that stops containing horizontal overflow** — a 3000px child scrolled the
page sideways by 500px — which is the exact regression `MB1 H1` exists to prevent. The clip rule
stays; the menus give way.
