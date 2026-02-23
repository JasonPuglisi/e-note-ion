# Assets

## Social preview (`social-preview.png`)

The GitHub repository social preview image was generated using
[Claude](https://claude.ai) (Anthropic). The image was iteratively refined
through several rounds of feedback. The final prompt used to produce it:

```
Generate a social media preview image at 1280×640 pixels for a GitHub
repository called "E•NOTE•ION". The name is a play on both "Vestaboard
Note" (a split-flap display) and the Carly Rae Jepsen album "E•MO•TION".

**Style:** 80s synthpop / E•MO•TION album aesthetic. Soft pastel gradient
background, pink fading to lavender or light blue. Dreamy, warm, slightly
retro. No photorealism — graphic design / illustration style.

**Center element:** A split-flap display showing "E-NOTE-ION" across 10
tiles: `E`, `-`, `N`, `O`, `T`, `E`, `-`, `I`, `O`, `N`. The dashes are
proper dark flap tiles with a `-` character. Two tiles appear mid-flip to
show motion — the O tile (4th) and the I tile (8th):
- Each tile is built from two half-panels (top and bottom), both showing
  the **outgoing** character, clipped to their respective halves so the
  character appears whole across the split.
- A **falling flap** overlay covers the top half, showing the **incoming**
  character's top half, rotated around its bottom edge using CSS `rotateX`
  with `perspective`. The flap should be nearly flat (almost done
  flipping) — use `rotateX(18deg)` for the O tile (N→O transition) and
  `rotateX(22deg)` for the I tile (H→I transition). Include a subtle
  darkening gradient on the flap face to sell the foreshortening.
- A 2px dark split line sits at the tile midpoint (z-index above panels).
- Tile bodies are dark near-black purple-dark. Characters in warm
  cream/white (`#f5e6cc`) with a subtle glow. Flap characters slightly
  warmer/dimmer (`#c9a572`).

**Title:** "E•NOTE•ION" in Bebas Neue bold retro typography, styled like
the E•MO•TION album logo. Use two filled pink circles (`#d94f7a`, 22px
diameter) as dot separators between E, NOTE, and ION. Font size ~96px,
dark color, slight text shadow. Dots should be vertically centered with
the text (use `top: -2px` to fine-tune alignment).

**Tagline** below in DM Sans 300 weight, ~22px, letter-spacing 2.5px:
"Automation for Vestaboard displays — with emotion" — no trailing period,
sentence case (capitalize Automation and Vestaboard only).

**Accents:** ~11 small colored square tiles (red `#e8453c`, orange
`#f28b30`, yellow `#f5c842`, green `#4caf6e`) scattered as confetti around
the composition, referencing Vestaboard's colored character codes. Vary
sizes (~13–20px), rotations, and opacity slightly. Keep outside the
central content area.

**Layout:** All content within a 40pt safe border. No watermark or label.
Clean centered composition — display above, title and tagline stacked
below.
```

## Icon (`icon.png`)

The app icon was generated using [Claude](https://claude.ai) (Anthropic).
The final prompt used:

```
Generate a square app icon at 256×256 pixels for a project called
E•NOTE•ION — a cron-based content scheduler for Vestaboard split-flap
displays. The name is a play on both "Vestaboard Note" and the Carly Rae
Jepsen album "E•MO•TION".

**Style:** Same 80s synthpop / E•MO•TION aesthetic as the social preview —
soft pastel pink-to-lavender gradient background
(`linear-gradient(135deg, #f9c5d1 0%, #f2a7c3 20%, #d9a7e0 50%,
#a7bde0 80%, #a0c8e8 100%)`), dreamy, warm, slightly retro. Add two soft
blurred light blobs for depth (pink top-left, purple bottom-right). No
photorealism — graphic design / illustration style.

**Shape:** Circular composition with a transparent background (PNG with
alpha). The gradient background and tile are contained within a circle —
no square edges. Should read clearly at small sizes like 32–64px.

**Center element:** A single split-flap display tile, 140×172px with 12px
border radius, showing the ❤ character in warm cream/white (`#f5e6cc`)
with a subtle glow
(`text-shadow: 0 0 20px rgba(245,220,180,0.7), 0 0 50px rgba(245,200,150,0.3)`).
Built from two half-panels:
- Top half: `linear-gradient(180deg, #1e1828 0%, #231c2e 100%)`, aligns
  character bottom to split
- Bottom half: `linear-gradient(180deg, #231c2e 0%, #1a1520 100%)`,
  aligns character top to split
- Each half clips its portion of the character so it appears whole across
  the midpoint
- A 2px dark split line (`rgba(0,0,0,0.8)`) sits at the tile midpoint,
  z-index above panels
- Subtle drop shadow:
  `0 8px 40px rgba(60,20,80,0.38), 0 2px 8px rgba(0,0,0,0.3)`

The tile should have comfortable padding within the circle — sized so the
gradient background shows evenly on all sides.

**No text, no wordmark — icon only.**
```
