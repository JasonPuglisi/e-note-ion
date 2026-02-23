# Display Design System

Guidelines for all e-note-ion content — contrib integrations, community
templates, and personal content alike.

The **Note** (3 rows × 15 cols) is the primary target. Design for it
first. The **Flagship** (6 rows × 22 cols) is supported and gets extra
layout guidance where relevant, but every template should work
beautifully on the Note.

This document is a living reference. Claude follows these guidelines
when writing or reviewing any content JSON, integration output, or
format strings.

---

## Spirit

e-note-ion is a display with emotion — playful, warm, and a little bit
musical. The board shows up in people's spaces: kitchens, desks, walls.
It should feel like a good friend giving you a heads-up, not a system
status page.

That said: the board is small and the flaps are physical. Clarity always
wins over cleverness. The goal is terse *and* warm, not terse *instead of*
warm.

---

## The one rule

**Every character should earn its place.**

The board is small. Whitespace is intentional. Clutter is a failure mode.
When in doubt, cut — but keep the feeling.

---

## Character set

The Vestaboard supports a fixed set of characters. Content must stay
within this set — unknown characters encode as blank.

**Letters:** A–Z (uppercase only)

**Digits:** 0–9

**Punctuation:** `! @ # $ ( ) - + & = ; : ' " % , . / ?`

**Special:**
- `❤` (code 62) — renders as a red heart on the Note, a degree sign
  (`°`) on the Flagship. Use `❤️` or `°` interchangeably in format
  strings; both encode to the same slot.
- Color squares — `[R]` `[O]` `[Y]` `[G]` `[B]` `[V]` `[W]` `[K]`
  (see Color section)

**Not available:** accented characters, em dash, middle dot, curly
quotes, arrows, or any Unicode outside the above list.

---

## Layout

### Row structure

Most templates follow a two-zone structure:

```
Row 1        → header: source/context identifier
Rows 2–3     → data: the actual content  (Note)
Rows 2–5     → data: the actual content  (Flagship)
```

The header tells the user *what* they're looking at. Data rows tell them
*the thing itself*.

**Note (3 rows):** One header + two data rows is the standard. This is
the design target — get it right here first. Avoid squeezing a third
data row; it usually means the content needs trimming, not expansion.

**Flagship (6 rows):** One header + up to five data rows. Use the extra
rows for genuinely useful detail. A template with three data rows and
two blank rows is fine — blank space is better than filler.

### Header conventions

- Lead with a color square that identifies the source or integration
  brand (see Color below), followed by a short all-caps label
- Keep it to one row; never wrap the header
- Name the *mode or source*, not the data: `[V] NOW PLAYING` ✓ — not
  `[V] TRAKT NOW PLAYING` ✗
- When the source is obvious from context (e.g. a standalone clock), the
  color square can be omitted — but a header row still grounds the layout

### Alignment

- Left-align all text — the board's natural reading direction
- Right-align numbers only when they form a column (e.g. departure times
  stacked vertically)
- Do not pad lines with trailing spaces to fill width

### Spacing

- Blank rows are intentional — use them to breathe on the Flagship
- Do not add blank rows just to fill space; the board should feel
  considered, not half-empty

---

## Color

Color squares (`[R]` `[O]` `[Y]` `[G]` `[B]` `[V]` `[W]` `[K]`) render
as a single filled square on the physical display. They're one of the most
expressive tools available — use them with intention.

### Semantic palette

| Tag | Color | Meaning |
|-----|-------|---------|
| `[G]` | Green | Positive, active, on time, go |
| `[R]` | Red | Alert, error, cancelled, urgent |
| `[O]` | Orange | Caution, approaching, mild concern |
| `[Y]` | Yellow | Warning, delayed, secondary alert |
| `[B]` | Blue | Informational, calm status |
| `[V]` | Violet | Expressive, creative, entertainment |
| `[W]` | White | Neutral, de-emphasized, structural |
| `[K]` | Black | Avoid — blends with the board background |

There is no universal default header color. Each integration should use
the color that fits its brand or data. If no color naturally fits, omit
the color square rather than defaulting to one arbitrarily.

**Data-driven color** (where the color *is* the data, e.g. a line color
or status indicator) may diverge from the semantic palette. That's fine;
document the mapping in the integration's sidecar `.md`.

### Restraint

- One color square per row is almost always enough
- Do not use color purely decoratively — every square should carry meaning
- Color squares count as characters: on a 15-column board, two squares
  leave only 13 for text — use them carefully

---

## Typography and wording

The board is ALL CAPS — lean into it. It has a voice: punchy, direct,
a little theatrical. Work with that, not against it.

### Tone

**Playful but not precious.** The display is expressive — that's the whole
point. Short wordplay, clean rhythm, a sense of occasion are all welcome.
Don't chase cleverness at the cost of clarity.

**Warm, not clinical.** Prefer phrasing that sounds like something a
person might say over something a system would print. `NOW PLAYING` feels
alive. `PLAYBACK STATUS: ACTIVE` does not.

**Terse, not cold.** Cut every word that doesn't earn its place, but don't
sand off the personality in the process. `GOOD MORNING` is two words and
zero wasted space.

**Musical resonance.** When there's a natural fit — show titles, time
references, transitions — a light musical touch suits the project's DNA.
Don't force it, but notice when it's there.

### Abbreviations

Abbreviate when the meaning survives:

| Instead of | Use |
|---|---|
| `SEASON 1 EPISODE 3` | `S1E3` |
| `8:30` (unambiguous context) | `8:30` |
| `MONDAY` | `MON` (when space is tight) |
| `TOMORROW` | `TMR` (when space is tight) |
| `MINUTES` | `MIN` |

Do not over-abbreviate: `DPRT` for `DEPARTS` saves two characters and
costs comprehension. Prefer dropping the word entirely over mangling it.

Strip leading articles from titles: `THE BEAR` → `BEAR`, `A QUIET PLACE`
→ `QUIET PLACE`. The article rarely adds meaning in this context.

### Time formatting

Use 24-hour time throughout: `08:30`, `14:30`, `00:00`. Always zero-pad
both the hour and minute. No AM/PM suffix. The colon (`:`) is available
in the character set.

### Numbers and units

- Strip leading zeroes from all numeric content — `3 TRAINS`, `S1E3`,
  `5MI` — **except times**, which are always zero-padded (see above)
- Omit units when they're unambiguous in context
- Temperatures: `72°` — the `°` character is available on the Flagship
  (code 62); on the Note, code 62 renders as `❤`, so prefer `72F` /
  `72C` there
- Distances: `1.2MI` or `1.2KM`
- Counts: plain numerals (`3 TRAINS`, not `THREE TRAINS`)

### Punctuation

- No terminal periods — statements end by ending
- Use `/` as a separator when two pieces of data share a row:
  `S1E3 / MON 14:30` — note time is zero-padded, episode ref is not
- Avoid commas — they read as hesitation
- Avoid the em dash — it's not in the character set; use `-` or `/`

### Truncation

Use the `truncation` field to control overflow:

| Mode | Use when |
|---|---|
| `ellipsis` | Live API data — the user should know content was cut |
| `word` | Hand-written static content with natural word boundaries |
| `hard` | Output is pre-fitted and overflow is impossible or intentional |

Default to `ellipsis` for all integration templates that pull live text
(show names, station names, external strings).

---

## Quick checklist

Before shipping a new template or integration:

- [ ] Designed for Note (3×15) first?
- [ ] Header row present with a color square (if the integration has a
  natural brand color) + short mode label?
- [ ] Color squares carry meaning, not decoration?
- [ ] Tone is warm and direct — not clinical, not trying too hard?
- [ ] No leading articles in titles (`A`, `AN`, `THE`)?
- [ ] Times in 24-hour zero-padded format (`14:30`, `08:30`, `00:00`)?
- [ ] Other numeric content has no leading zeroes (`S1E3`, not `S01E03`)?
- [ ] All characters within the supported set?
- [ ] `truncation: ellipsis` for all live-data strings?
- [ ] Priority and timeout paired correctly for content urgency?
- [ ] Sidecar `.md` documents any data-driven color mapping?
