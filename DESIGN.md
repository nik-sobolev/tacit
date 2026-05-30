# Tacit — Design System

> Source of truth for brand, visual language, and image generation prompts.
> Derived from the live UI. All values are exact — extracted directly from styles.css.

---

## Aesthetic Identity

**The feeling:** A high-context intelligence layer. Like a Bloomberg Terminal crossed with a Moleskine — dense, purposeful, dark. Not a consumer app. Not a dashboard. A thinking environment for people who process more information than most.

**What it is not:** Colorful. Playful. Gradient-heavy. Clean-white minimal. Corporate blue. It lives in the dark, uses a single warm accent color, and earns every pixel of chrome it shows.

**One-line prompt seed:** `dark knowledge canvas, editorial intelligence, deep navy background, single burnt-orange accent, dot-grid pattern, floating cards with semantic color coding, serif display type`

---

## Color Palette

### Core

| Token | Hex | Name | Use |
|-------|-----|------|-----|
| `--bg` | `#0f1419` | Obsidian | Canvas background, page base |
| `--bg-elevated` | `#1a1f26` | Obsidian Lifted | Cards, input fields |
| `--surface` | `#161b22` | Slate | Panels, headers, sidebars |
| `--surface-hover` | `#1f2530` | Slate Hover | Interactive surface states |

### Text

| Token | Hex | Name | Use |
|-------|-----|------|-----|
| `--text` | `#e6edf3` | Frost | Primary body text |
| `--text-secondary` | `#8b949e` | Ash | Secondary info, summaries |
| `--text-tertiary` | `#6e7681` | Smoke | Metadata, labels, placeholders |

### Brand Accent (Burnt Orange)

| Token | Hex | Name | Use |
|-------|-----|------|-----|
| `--primary` | `#c05621` | Ember | Buttons, active states, focus rings, brand icon |
| `--primary-dark` | `#9c4419` | Charcoal Ember | Hover on primary buttons |
| `--primary-light` | `#d87743` | Ember Light | Links, tag text, accent highlights |
| `--accent` | `#d4a574` | Sand | Warm secondary; bold text in chat, brand gradient end |

### Borders & Structure

| Token | Hex | Name | Use |
|-------|-----|------|-----|
| `--border` | `#30363d` | Graphite | Standard borders, dividers |
| `--border-subtle` | `#21262d` | Graphite Subtle | Low-contrast dividers within surfaces |

### Content-Type Colors (Card Top Borders)

These are platform-source indicators — used only as 2px top accent stripes on cards and as badge text/borders in detail panels.

| Type | Hex | Name |
|------|-----|------|
| YouTube | `#ff4444` | Signal Red |
| TikTok | `#69c9d0` | Cyan Mint |
| Instagram | `#e1306c` | Magenta Rose |
| Webpage | `#4a9eff` | Sky Blue |
| Document | `#7c5cbf` | Muted Violet |
| Note / Text | `#d4a574` | Sand (brand accent) |

### Semantic States

| State | Hex | Use |
|-------|-----|-----|
| Error / Delete hover | `#ff6b6b` | Destructive actions |
| Warning / Duplicate | `#e6a817` | Duplicate detection border |
| Success toast border | `rgba(63,185,80,0.4)` | Positive feedback |

### Gradients

**Brand icon gradient:**
```
linear-gradient(135deg, #c05621, #d4a574)
```
Direction: top-left to bottom-right. Ember to Sand. Used on the brand icon square only.

**Canvas ambient glow:**
```
radial-gradient(circle at 20% 30%, rgba(192,86,33,0.06) 0%, transparent 50%),
radial-gradient(circle at 80% 70%, rgba(212,165,116,0.04) 0%, transparent 50%)
```
Subtle warm breathing on the canvas background. Not visible unless you look for it.

---

## Typography

### Typefaces

| Role | Family | Weights | Character |
|------|--------|---------|-----------|
| Display | Crimson Pro | 400, 600, 700 | Serif, editorial, intelligent. Used for titles, headers, empty state H2. |
| Body | Work Sans | 400, 500, 600, 700 | Sans-serif, clean, readable. Everything else. |
| Fallback | Georgia → serif / system-ui → sans-serif | — | Always specify both |

### Scale

| Use | Size | Weight | Font | Notes |
|-----|------|--------|------|-------|
| Brand name | 20px | 700 | Display | Letter-spacing -0.3px |
| Card title | 13px | 600 | Body | 2-line clamp |
| Card summary | 11.5px | 400 | Body | 3-line clamp |
| Chat message | 13px | 400 | Body | Line-height 1.55 |
| Detail title | 18px | 600 | Display | Line-height 1.3 |
| Section label | 10–11px | 600–700 | Body | UPPERCASE, letter-spacing 0.5–0.8px |
| Tag / badge | 9–11px | 500–600 | Body | UPPERCASE or title case |
| Metadata | 10–11px | 400 | Body | Smoke color |

---

## Spacing & Layout

### App Structure
```
Full viewport height, no scroll
├── Header bar — 52px fixed
└── App body — remaining height
    ├── Chat panel — 300px default, resizable 240–520px
    ├── Resize handle — 5px
    └── Canvas viewport — flex: 1
```

### Component Spacing Rhythm

- **Tight:** 4px (gap between chips, small button padding)
- **Compact:** 8px (card internal gaps, icon button size 32px)
- **Standard:** 12px (panel padding, message gap)
- **Comfortable:** 16px (content sections, header padding)
- **Generous:** 20–24px (section breaks in detail panels)

### Border Radius

| Element | Radius |
|---------|--------|
| Cards | 12px |
| Buttons (primary) | 5–8px |
| Icon buttons | 6px |
| Input fields | 8px |
| Tags / chips | 10px (pill) |
| Badges | 4px |
| Brand icon | 6px |
| Thumbnails | 8px |
| Toasts | 8px |

---

## Component Patterns

### Card (Canvas Node)

The primary content unit. Width: 260px fixed.

```
┌─────────────────────────────┐  ← border-radius: 12px
│ ██ 2px type-color stripe    │  ← YouTube=red, Webpage=blue, etc.
├─────────────────────────────┤
│ [icon] TYPE         [cat]  ✕│  ← card-header: 8px 10px 4px
├─────────────────────────────┤
│ [16:9 thumbnail if present] │  ← full width, object-fit: cover
├─────────────────────────────┤
│ Card Title (600, 13px)      │  ← 2-line clamp
│ Summary text (400, 11.5px)  │  ← 3-line clamp, Ash color
│ [tag] [tag] [tag]           │  ← orange pill tags
├─────────────────────────────┤
│ source.domain.com           │  ← Smoke, 10px, ellipsis
└─────────────────────────────┘
```

**Hover state:** border-color shifts to `#c05621`, box-shadow adds `0 8px 24px rgba(0,0,0,0.5)` + orange glow ring `0 0 0 1px rgba(192,86,33,0.3)`.

**Active category filter:** non-matching cards → `opacity: 0.15`, `filter: grayscale(0.8)`.

### Canvas Background

```
background: #0f1419
+ radial-gradient warm ambient (ember, sand — very subtle, 4–6% opacity)
+ dot grid: radial-gradient(circle, rgba(255,255,255,0.06) 1px, transparent 1px)
  background-size: 28px 28px
```

The dot grid is white at 6% opacity — invisible in bright environments, visible in dark. It gives the canvas texture without competing with content.

### Edges (Semantic Connections)

SVG lines between related cards. Not defined in CSS — rendered by JS into `<svg class="edges-layer">`. Style: thin, low-opacity lines in the primary or border color. Dashed where connection is weak.

### Chat Panel

Left-anchored sidebar. Dark surface (`#161b22`), subtle border-right. Three modes: Chat, History, People — toggled by header buttons. The chat mode shows message bubbles:

- **User messages:** `background: #c05621`, white text, `border-bottom-right-radius: 3px` (speech bubble shape)
- **Assistant messages:** `background: #1a1f26`, frost text, `border: 1px solid #21262d`, `border-bottom-left-radius: 3px`
- **Bold in assistant:** `color: #d4a574` (sand accent)

### Buttons

**Primary (CTA):**
```
background: #c05621
color: white
border-radius: 5–8px
font-weight: 600
font-size: 12–13px
hover: #9c4419
```

**Icon button:**
```
background: transparent
border: 1px solid #30363d
border-radius: 6px
size: 32×32px
color: #8b949e
hover: background #1f2530, color #e6edf3, border #c05621
```

**Ghost/text button:**
```
background: transparent
border: none
color: #6e7681
font-size: 12px, weight 500
hover: background #1f2530, color #e6edf3
```

**Primary text button** (e.g. "+ New"):
```
color: #c05621
hover: background rgba(192,86,33,0.1), color #d87743
```

### Tags & Badges

**Content tags** (on cards):
```
background: rgba(192,86,33,0.15)
color: #d87743
border: 1px solid rgba(192,86,33,0.25)
border-radius: 10px (pill)
font-size: 10px
padding: 2px 7px
```

**Category badge** (top-right of card):
```
font-size: 9px, weight 500
background: dynamic per category
border-radius: 8px
padding: 1px 6px
```

**Type badge** (detail panel):
```
font-size: 10px, weight 700
UPPERCASE, letter-spacing: 1px
border: 1px solid [type-color at 30% opacity]
color: [type-color]
border-radius: 10px
padding: 2px 8px
```

**Relationship tag** (People view):
```
background: rgba(192,86,33,0.12)
color: #d87743
border: 1px solid rgba(192,86,33,0.25)
border-radius: 4px
font-size: 11px
padding: 2px 7px
```

### Active / Selected State

Consistent pattern throughout: left border becomes `#c05621` (3px), background gets `rgba(192,86,33,0.07)`.

Used in: history items, category items.

### Shadows

```
--shadow:    0 4px 12px rgba(0,0,0,0.4)
--shadow-lg: 0 12px 32px rgba(0,0,0,0.5)
Card hover:  0 8px 24px rgba(0,0,0,0.5)
```

Everything on the dark background needs strong shadow depth. No subtle box shadows — they disappear.

---

## Animation & Motion

| Element | Property | Duration | Easing |
|---------|----------|----------|--------|
| Sidebars slide | transform | 250ms | ease |
| Chat panel collapse | width | 200ms | — |
| Border/color transitions | all | 150ms | — |
| Loading dots | translateY | 1.2s | infinite |
| Spinner | rotate | 0.8s | linear infinite |
| Highlight pulse | box-shadow | 0.5s × 2 | ease-out |
| Toast appear | opacity + translateY | 200ms | — |

Motion is subtle and fast. Nothing bounces. Nothing overshoots. The app feels immediate.

---

## Image Generation Prompts

### Base Style Prompt (use as foundation for any Tacit visual)

```
dark knowledge canvas interface, deep obsidian background #0f1419, 
burnt orange accent color #c05621, floating dark cards with colored 
top border stripes, dot grid pattern on dark background, 
serif editorial typography mixed with clean sans-serif, 
left-side chat panel with dark surface, canvas with semantic 
connection edges between nodes, cinematic dark UI, 
no gradients except subtle warm ambient glow, 
professional intelligence tool aesthetic
```

### Hero / Marketing Screenshot Prompt

```
screenshot of a dark second-brain application, obsidian dark background,
floating content cards arranged on a dot-grid canvas with semantic edge 
connections between them, cards have colored 2px top stripes (red, blue, 
cyan, orange) indicating content type (YouTube, Webpage, TikTok, Article),
left panel shows AI chat conversation with burnt orange send button,
header bar with URL input and "Add to Canvas" button in burnt orange,
Crimson Pro serif font for titles, Work Sans sans-serif for UI,
cards show article thumbnails, titles, summaries, and orange pill tags,
dark surfaces #161b22 and #1a1f26, primary accent #c05621,
ultra-sharp, high-fidelity UI render, photorealistic interface mockup
```

### Brand Icon Prompt

```
small square app icon, 6px border radius, 
135-degree gradient from #c05621 burnt orange to #d4a574 warm sand,
minimal, no text, no symbol, just the gradient,
dark background context
```

### Card Component Prompt

```
single floating UI card, dark background #1a1f26, 
1px border #30363d, 12px border radius,
2px top border stripe in bright blue (#4a9eff) for webpage type,
card header showing small icon, "WEBPAGE" label in smoke gray uppercase 10px,
small teal category badge top right,
16:9 thumbnail image area,
bold white title text 13px two lines,
gray summary text 11.5px three lines,
burnt orange pill tags at bottom,
source URL in small gray text,
subtle hover state with orange glow border,
dark floating card on dot-grid canvas background
```

### Canvas Overview Prompt

```
aerial view of a knowledge canvas, dark obsidian background with subtle 
dot grid pattern (28px spacing, white dots at 6% opacity),
multiple floating dark cards connected by thin SVG edge lines,
cards clustered by semantic similarity,
burnt orange warm ambient glow in upper-left and lower-right corners,
cards vary in position — some clustered, some isolated,
edges between related cards in low-opacity lines,
left edge shows edge of dark chat sidebar,
cinematic depth of field, high-fidelity interface render
```

### Empty State Prompt

```
dark knowledge canvas interface showing empty state,
centered brain emoji at 48px, low opacity,
serif headline "Your second brain is empty" in muted gray,
small rounded pill buttons showing content types: YouTube, TikTok, Instagram, Articles,
dot grid background, ambient orange glow,
minimalist, instructional, calm
```

### Schematic / Wireframe Prompt

```
dark UI wireframe schematic for a knowledge canvas application,
three-panel layout: left chat panel 300px, center canvas with floating cards, 
right detail panel slides in from edge,
top header bar 52px with URL input and action buttons,
annotated with spacing values and color tokens,
dark background, burnt orange accent highlights on interactive elements,
technical blueprint aesthetic, design system documentation style
```

---

## Design Principles (for prompt writing and design decisions)

1. **One warm color.** Ember orange is the only warm note. Everything else is cool-dark. Never add a second accent color.

2. **Density without clutter.** Cards are 260px wide. Text is small but legible. The UI earns its density by being purposeful — every element has a reason to exist.

3. **Platform colors are signals, not decoration.** YouTube red, TikTok cyan, Webpage blue — these are data, not brand. They appear only as 2px top stripes and type badges. Never use them for backgrounds or buttons.

4. **Serif for titles, sans for UI.** Crimson Pro appears only for prominent content titles and the brand name. Work Sans handles everything functional.

5. **Dark surfaces need depth.** Three background levels: canvas (`#0f1419`), surfaces (`#161b22`), cards (`#1a1f26`). Without this layering, everything flattens. Never collapse two levels into one.

6. **Interactive elements whisper until hovered.** Delete buttons are `opacity: 0`. Borders are graphite. Things reveal themselves on approach — the canvas doesn't shout.

7. **The dot grid is the canvas.** It gives infinite space texture and spatial awareness without competing with content. 28px spacing, 1px dots, 6% opacity white.

---

## File Reference

| File | Purpose |
|------|---------|
| `frontend/static/styles.css` | All design tokens, component styles |
| `frontend/static/index.html` | App structure and component markup |
| `frontend/static/app.js` | Canvas rendering, card generation, edge drawing |
| `docs/tacitscreen.png` | Live app screenshot reference |
