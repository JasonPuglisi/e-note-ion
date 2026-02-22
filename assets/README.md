# Assets

## Social preview (`social-preview.png`)

The GitHub repository social preview image was generated using
[Claude](https://claude.ai) (Anthropic). The image was iteratively refined
through several rounds of feedback. The final prompt used to produce it:

```
Generate a social media preview image at 1280×640 pixels for a GitHub
repository called "e-note-ion". The name is a play on both "Vestaboard
Note" (a split-flap display) and the Carly Rae Jepsen album "E•MO•TION".

Style: 80s synthpop / E•MO•TION album aesthetic. Soft pastel gradient
background, pink fading to lavender or light blue. Dreamy, warm, slightly
retro. No photorealism — graphic design / illustration style.

Center element: A split-flap display showing "E-NOTE-ION" across 10 tiles:
E, -, N, O, T, E, -, I, O, N. The dashes are proper dark flap tiles with a
- character. Two tiles appear mid-flip to show motion — the O tile (4th)
and the I tile (8th):

Each tile is built from two half-panels (top and bottom), both showing the
outgoing character, clipped to their respective halves so the character
appears whole across the split.

A falling flap overlay covers the top half, showing the incoming
character's top half, rotated around its bottom edge using CSS rotateX with
perspective. The flap should be nearly flat (almost done flipping) — use
rotateX(18deg) for the O tile (N→O transition) and rotateX(22deg) for the
I tile (H→I transition). Include a subtle darkening gradient on the flap
face to sell the foreshortening.

A 2px dark split line sits at the tile midpoint (z-index above panels).

Tile bodies are dark near-black purple-dark. Characters in warm
cream/white (#f5e6cc) with a subtle glow. Flap characters slightly
warmer/dimmer (#c9a572).

Title: "E·NOTE·ION" in Bebas Neue bold retro typography, styled like the
E•MO•TION album logo. Use two small filled pink circles (#d94f7a) as dot
separators between E, NOTE, and ION. Font size ~96px, dark color, slight
text shadow.

Tagline below in DM Sans 300 weight, ~22px: "Automation for Vestaboard
displays — with emotion" — no trailing period, sentence case (capitalize
Automation and Vestaboard only).

Accents: ~11 small colored square tiles (red #e8453c, orange #f28b30,
yellow #f5c842, green #4caf6e) scattered as confetti around the
composition, referencing Vestaboard's colored character codes. Vary sizes
(~13–20px), rotations, and opacity. Keep outside the central content area.

Layout: All content within a 40pt safe border. No watermark or label.
Clean centered composition — display above, title and tagline stacked
below.
```

## Icon (`icon.png`)

The app icon was generated using [Claude](https://claude.ai) (Anthropic),
continuing the same chat session as the social preview to preserve aesthetic
context. The final prompt used:

```
Now generate a square app icon at 256×256 pixels for the same project.

Center element: A single split-flap display tile, dark (near-black
purple-dark), showing the ❤ character in warm cream/white with a subtle
glow. Include a horizontal split line across the middle of the tile. The
tile should be large and prominent, filling most of the icon, with
slightly rounded corners and a subtle drop shadow.

Use the same pastel pink-to-lavender gradient background and overall
aesthetic as the social preview. No text, no wordmark — icon only. Should
read clearly at small sizes like 32–64px.
```
