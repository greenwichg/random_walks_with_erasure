# Radix modal menus, `overflow-x: clip`, and two real costs

**Status:** both fixed. The header menus are non-modal; the scroll lock's scrollbar gutter is
neutralised in CSS for every modal overlay in the app.

> **Correction.** An earlier version of this document, and the commit `fbf63f6` it shipped with,
> said the bug was that a modal menu *threw the reader to the top of the page and never restored the
> position*. **That was wrong.** The measurement behind it clicked a trigger that was off-screen, and
> Playwright scrolls a target into view before clicking it — so the jump was the harness's, not the
> app's. Isolated afterwards: with `modal={true}` restored, the test whose only assertion is scroll
> preservation **passes**. What follows is what actually survives measurement.

---

## 1. The mechanism

Every Radix modal overlay mounts `react-remove-scroll`, which injects, unlayered, at runtime:

```css
body[data-scroll-locked] {
  overflow: hidden !important;
  margin-right: <scrollbar-width>px !important;
}
```

That is meant to be invisible. Normally body's overflow **propagates to the viewport**: the viewport
stops scrolling, its scrollbar disappears, and `margin-right` fills exactly the gap the scrollbar
left, so nothing moves.

Propagation only happens when the **root** element's overflow is `visible` in *both* axes. And
`app/globals.css` has:

```css
html { overflow-x: clip; }   /* MB1 H1 — guarantee zero horizontal scrolling */
```

So propagation is off and the declaration lands on `<body>` itself. The viewport scrollbar never
goes away — and the gutter compensates for nothing.

## 2. Cost one: the page shifts sideways

Measured A/B against the real build, injecting the 15px gutter a classic-scrollbar desktop produces:

| | `<main>` right edge | body `margin-right` |
|---|---|---|
| without the override | 1280 → **1265** (−15px) | 15px |
| with the override | 1280 → **1280** (0px) | 0px |

Every filter on Discover, History, Search and Stories is a modal `FilterSelect`, so this is every
filter — the whole page jumps sideways each time one opens.

**Only where scrollbars are classic**: Windows, Linux, and the installed desktop PWA. Headless
Chromium and macOS use overlay scrollbars and report a 0px gap, which is why no test caught it until
one was written to inject the gutter deliberately.

**Fix:** an unlayered override in `globals.css`:

```css
html body[data-scroll-locked] {
  overflow-x: clip !important;
  overflow-y: visible !important;
  margin-right: 0 !important;
}
```

`html body[…]` (0,1,2) outranks their `body[…]` (0,1,1); both carry `!important`, so specificity
decides. It **must** stay outside `@layer` — their stylesheet is unlayered, and an unlayered rule
beats a layered one however specific.

## 3. Cost two: the control leaves the accessibility tree

A modal menu also marks the rest of the document `aria-hidden="true"`. For the profile menu that
means the header — including the avatar whose menu is open — is not in the accessibility tree at
all. `getByRole` cannot find it; nor can a screen reader.

That is the demonstrated half of "disappears **or becomes inaccessible**".

**Fix:** `modal={false}` on the profile menu and the notifications panel. Neither is a modal; they
are navigation, and neither should trap focus or hide the document.

## 4. Why not fix it once, in the primitive

Defaulting `components/ui/dropdown-menu.tsx` to non-modal was tried. It **breaks the Stories filter
reset**: `stories-filter-state.spec.ts` → "resetting a filter cleans the parameter out of the URL"
passes with `modal={true}` and fails without it, deterministically, 3/3. At failure the DOM contains
no menu at all — the second trigger click races Radix's close sequence once the modal layer is gone.

So filters keep modal semantics and are covered by §2 instead. Their `aria-hidden` behaviour is left
as-is: for a genuinely modal menu that is the intended semantic, not a defect.

## 5. Do not "fix" it in the base CSS

The tempting move is to drop `overflow-x: clip` from `html` and leave it on `body`, restoring
propagation. **Measured: that stops containing horizontal overflow** — a 3000px child scrolled the
page sideways by 500px — which is the exact regression MB1 H1 exists to prevent. The clip rule
stays.

## 6. Tests

`e2e/specs/header.spec.ts`:

- **Profile menu** — header stays out of `aria-hidden`, menu on screen and anchored while the page
  scrolls, phone viewport, no lock left behind after close/reopen. Rebuilding with `modal={true}`
  fails three of the four.
- **Modal menus and the scroll lock** — injects the dependency's exact stylesheet at a 15px gap and
  asserts zero shift. Disabling the override fails it.

Both guard a cascade fight against a stylesheet a dependency injects at runtime, which is the kind
of fix that dies silently on a version bump.
