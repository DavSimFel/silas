# Silas — Design Language Specification: "Quiet"

> Version 1.0 — Addendum to specs.md §0.5 and §1.6

---

## 1. Philosophy

Quiet is the design language for Silas. It takes cues from Apple's Human Interface Guidelines but serves a fundamentally different product: not a chat app, but an **agent interface** — a surface where a human directs an intelligence.

**Core thesis:** The best AI interface is one you barely notice. Information appears when you need it and recedes when you don't. The UI has no opinion about itself — all attention goes to the content and the work.

### 1.1 Principles

| # | Principle | Implication |
|---|-----------|-------------|
| 1 | **Content is the interface** | No decorative chrome. Text, cards, and space do all the work. Remove everything that isn't content or an action. |
| 2 | **Density on demand** | Default state is spacious. Each layer of detail requires an explicit gesture to reveal. Never force information density. |
| 3 | **Hierarchy through weight** | Font weight and opacity create hierarchy. Not color, not borders, not boxes. Three levels: primary (0.92), secondary (0.55), tertiary (0.30). |
| 4 | **One tint, one meaning** | The accent color (`#7ecbff`) means exactly one thing: "this is actionable." Buttons, links, interactive elements. Nothing else. |
| 5 | **Materials over colors** | Surfaces are defined by translucency and blur, not solid fills. Active elements feel luminous. Inactive elements feel recessed. |
| 6 | **Motion as physics** | Everything has mass. Cards slide, content fades, panels push. Spring-based easing. Nothing snaps, nothing teleports. |
| 7 | **Invisible until needed** | Status, metadata, history, settings — all exist but none demand attention by default. |

### 1.2 What Quiet Is Not

- Not a chat app. No chat bubbles for agent responses.
- Not a dashboard. No widgets, no grids, no persistent metrics.
- Not a settings app. Configuration is conversational or automated.
- Not playful. No emoji-heavy UI, no rounded-bubbly elements, no gamification.

---

## 2. User Workflows

Six workflows, ordered by frequency. Every design decision must serve these.

### 2.1 Quick Exchange (80% of interactions)

Open → type → answer → close. Under 30 seconds.

**UI requirements:**
- Input visible and focused immediately on open
- Response appears inline, full-width, no container
- No UI elements compete for attention
- History is scrollable but visually receded (lower opacity for older turns)

### 2.2 Delegate & Forget

"Handle this" → acknowledge → background work → notification when done or blocked.

**UI requirements:**
- Acknowledgment is a single subtle line, not a card
- Active work shown as ambient indicator (top strip), not a list
- Completion notification is a card that can be dismissed with one tap
- If user returns mid-work: current state visible without scrolling

### 2.3 Approve / Decide (highest urgency)

Notification pulls user in → context → decision → leave. Under 10 seconds.

**UI requirements:**
- Approval card contains enough context to decide without reading history
- Two primary CTAs maximum (accept / reject). Alternatives in expandable section
- Card anatomy per §0.5.3 (intent, risk, rationale, consequence labels)
- Cards are glass surfaces that float above content — visually distinct layer
- After decision: card collapses, confirmation appears briefly, then fades

### 2.4 Check In

"What's going on?" → see status → leave. Under 15 seconds.

**UI requirements:**
- Ambient status strip is always present: "{n} active · {n} needs review"
- Tapping strip reveals work panel (rises from bottom)
- Each work item is a single line. Tap to expand in-place.
- Panel is a sheet, not a page navigation

### 2.5 Deep Collaboration

Extended session with code, documents, plans. Minutes to hours.

**UI requirements:**
- Composer expands to multi-line naturally
- Code blocks render with syntax highlighting and copy button
- Long responses have internal structure (headings, collapsible sections)
- Side panel available for reference material (slides in from right)
- Scroll position is preserved — UI doesn't jump on new content

### 2.6 Recall

"What did we say about X?" — search and browse memory.

**UI requirements:**
- Slash command or search icon triggers memory search
- Results appear as a list of context snippets with source and date
- Tapping a result shows full context
- Search doesn't navigate away from the stream — it overlays

---

## 3. Visual Identity

### 3.1 Color System

```
Background:        #0a0f1e       Near-black blue. The void.
Surface:           rgba(255,255,255, 0.06) + blur(40px)    Glass layer 1.
Surface raised:    rgba(255,255,255, 0.10) + blur(60px)    Glass layer 2. Cards, panels.
Surface active:    rgba(255,255,255, 0.14) + blur(60px)    Hover/press state on surfaces.

Text primary:      rgba(255,255,255, 0.92)    Content you're reading now.
Text secondary:    rgba(255,255,255, 0.55)    Metadata, timestamps, labels.
Text tertiary:     rgba(255,255,255, 0.30)    Placeholders, disabled, ghost text.

Tint:              #7ecbff        Actionable elements only.
Tint hover:        #9dd8ff        Hover state on tint elements.
Tint subtle:       rgba(126,203,255, 0.12)    Tint background (button fills, highlights).
Tint glow:         rgba(126,203,255, 0.06)    Ambient glow behind active elements.

Status green:      rgba(52,199,89, 0.85)      Connected / success. Only in status dot.
Status amber:      rgba(255,204,0, 0.85)      Connecting / warning. Only in status dot.
Status red:        rgba(255,69,58, 0.85)       Error / offline. Only in status dot.
```

**Rule:** Status colors appear ONLY in the status dot and risk-level indicators on cards. Never in text, backgrounds, or borders.

### 3.2 Typography

```
Font stack:        -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', system-ui, sans-serif
Font mono:         'SF Mono', 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace

Scale:
  xs:    12px / 16px    Timestamps, badges
  sm:    13px / 18px    Secondary labels, metadata
  base:  15px / 22px    Body text, messages
  md:    17px / 24px    Section headers in responses
  lg:    20px / 28px    Card titles, panel headers
  xl:    24px / 32px    Empty state headline

Weights:
  light:     300    Tertiary content, ambient text
  regular:   400    Body text
  medium:    500    Labels, secondary emphasis
  semibold:  600    Headings, card titles, CTAs
  bold:      700    Primary emphasis (sparingly)
```

**Rule:** Maximum two weights in any single component. Weight creates hierarchy, not decoration.

### 3.3 Spacing

```
Unit:    4px base grid. All spacing is multiples of 4.

xs:      4px     Inline spacing, icon gaps
sm:      8px     Tight element groups
md:      12px    Related content within a block
lg:      16px    Between content blocks
xl:      24px    Between sections
2xl:     32px    Major section breaks
3xl:     48px    Page-level breathing room
```

### 3.4 Radii

```
Small:     8px     Buttons, chips, inline elements
Medium:    14px    Cards, panels, input fields
Large:     20px    Bottom sheets, modal panels
Full:      9999px  Status dots, badges, pills
```

### 3.5 Shadows & Depth

No traditional box shadows. Depth is communicated through:
1. **Backdrop blur** — stronger blur = higher layer
2. **Surface opacity** — more opaque = more elevated
3. **Subtle border** — `1px solid rgba(255,255,255,0.08)` on elevated surfaces only

```
Layer 0 (background):   No blur, no border. The void.
Layer 1 (content):      blur(20px), rgba(255,255,255,0.04), no border
Layer 2 (surface):      blur(40px), rgba(255,255,255,0.06), border 0.08
Layer 3 (raised):       blur(60px), rgba(255,255,255,0.10), border 0.10
Layer 4 (overlay):      blur(80px), rgba(255,255,255,0.14), border 0.12
```

### 3.6 Motion

```
Easing:
  spring:    cubic-bezier(0.22, 1, 0.36, 1)     Default for all motion
  ease-out:  cubic-bezier(0, 0, 0.2, 1)          Exit animations
  ease-in:   cubic-bezier(0.4, 0, 1, 1)          Entry animations (rare)

Duration:
  instant:   100ms    Hover, press, color changes
  fast:      200ms    Chip selection, small toggles
  default:   300ms    Content fade, card expand/collapse
  slow:      400ms    Panel slide, sheet rise, page transitions
  deliberate: 600ms   Empty state pulse, loading shimmer

Rules:
- Enter: fade in + translate Y (12px → 0). Spring easing.
- Exit: fade out. Ease-out. No translate (things disappear in place).
- Expand: height auto-animate. Spring easing. Content inside fades in 100ms after container opens.
- Collapse: reverse of expand but faster (0.7× duration).
- Panel: translateX (100% → 0) for side panels. translateY (100% → 0) for bottom sheets.
```

**Rule:** `prefers-reduced-motion` must be respected. All motion reduces to instant opacity changes.

---

## 4. Components

### 4.1 Stream (Primary Surface)

The conversation feed. Not a chat — a **command log**.

```
┌──────────────────────────────────────┐
│                                      │
│  What should I work on?              │  ← Empty state (centered, tertiary)
│                                      │
│──────────────────────────────────────│
│                                      │
│  "Check my emails"            14:23  │  ← User input: right-aligned, secondary text, small
│                                      │
│  Found 3 emails needing attention.   │  ← Agent response: full-width, primary text, no container
│                                      │
│  ┌─────────────────────────────────┐ │
│  │ RE: Q4 Budget Review           │ │  ← Interactive card: glass surface, Layer 3
│  │ From: heimo@feldhofer.co       │ │
│  │ Action needed: approve figures │ │
│  │                                │ │
│  │ [Review draft]    [Skip]       │ │  ← CTAs: tint for primary, tertiary for secondary
│  └─────────────────────────────────┘ │
│                                      │
│  ▸ 2 more emails (tap to expand)     │  ← Collapsed group: secondary text
│                                      │
└──────────────────────────────────────┘
```

**User input styling:**
- Right-aligned
- Text secondary opacity
- Font size: `sm` (13px)
- No background, no bubble, no container
- Timestamp inline, tertiary

**Agent response styling:**
- Left-aligned, full-width
- Text primary opacity
- Font size: `base` (15px)
- No background, no bubble, no container
- Responses with structure (lists, code, headings) render with proper semantic HTML
- Expandable detail sections: collapsed by default, indicated by `▸` prefix, secondary text

**History fade:** Messages older than the current exchange reduce to secondary opacity. Messages older than 5 exchanges reduce to tertiary. Tapping/scrolling into old messages restores full opacity.

### 4.2 Cards

Interactive elements that require user action. Glass surfaces on Layer 3.

**Anatomy:**
```
┌──────────────────────────────────┐
│  Intent headline              🟡 │  ← Title (semibold, lg) + risk dot
│  Rationale text in one line      │  ← Secondary text
│                                  │
│  ▸ Details                       │  ← Expandable (collapsed for low/medium risk)
│                                  │
│  [Primary CTA]         [Decline] │  ← Tint button vs tertiary text button
└──────────────────────────────────┘
```

**Risk dot colors:**
- `low`: no dot (invisible by default)
- `medium`: status amber
- `high`: status red
- `irreversible`: status red + pulsing glow

**Card transitions:**
- Appear: slide up 12px + fade in, 300ms spring
- Decide: collapse to single confirmation line, 200ms ease-out
- Confirmation line: "✓ Draft sent" — secondary text, fades to tertiary after 3s

### 4.3 Status Strip

Ambient awareness. Persistent at top. Barely visible until needed.

```
Default (all clear):     [nothing — strip is invisible]
Work active:             2 active · 1 needs review
Error state:             ● Connection lost
```

- Font size: `xs` (12px), text tertiary
- When items need review: "needs review" portion uses tint color
- Tap → work panel rises from bottom

### 4.4 Work Panel (Bottom Sheet)

Slides up when status strip is tapped. Layer 4 (overlay).

```
┌──────────────────────────────────┐
│  ━━━                             │  ← Drag handle (centered, subtle)
│  Active Work                     │  ← Title: semibold, lg
│                                  │
│  ● Checking emails          0:12 │  ← Active: status dot + name + elapsed
│  ● Drafting Q4 response     0:03 │
│  ◆ Budget approval needed        │  ← Needs review: tint diamond
│                                  │
│  ▸ Completed today (4)           │  ← Collapsed section
└──────────────────────────────────┘
```

- Snap points: 40% height (peek), 85% height (full), 0% (dismissed)
- Background dismissible (tap outside to close)
- Each item: tap to expand in-place (shows subtasks, logs, artifacts)

### 4.5 Composer

The input surface. Minimal by default, expands on demand.

```
Default state:
┌──────────────────────────────────┐
│  Message Silas…              [→] │  ← Thin line, placeholder, send arrow
└──────────────────────────────────┘

Active state (typing):
┌──────────────────────────────────┐
│  Check my emails and            │  ← Multi-line capable, auto-grows
│  draft replies to anything      │
│  urgent                          │
│  [📎]                       [→] │  ← Attachment button appears, send button
└──────────────────────────────────┘
```

- Default: single line, no visible border. Just placeholder + cursor.
- On focus: subtle border appears (surface border), content above dims slightly
- On typing: auto-grows to max 5 lines, then scrolls internally
- Send button: tint color, appears only when input is non-empty
- Attachment button: appears only on focus
- Slash commands: typing `/` shows command palette overlay

### 4.6 Side Panel

Desktop only. Reference material, memory, settings. Hidden by default.

- Slides in from right, pushing content left (not overlaying)
- Width: 380px fixed
- Layer 3 (raised surface)
- Contains tabs: Memory, Active Work, Session Info
- Dismissible via close button or swipe right

### 4.7 Empty State

When the stream has no content.

```
                    🪶

          What should I work on?
```

- Feather: centered, 48px, subtle breathing animation (opacity 0.4 → 0.7, 3s cycle)
- Text: centered, `xl` (24px), light weight, tertiary opacity
- No other UI elements visible except the composer

### 4.8 Loading / Thinking State

When Silas is processing.

```
  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
    ●  ●  ●               ← Three dots, sequential fade, tint color at 0.4 opacity
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

- Appears where the response will be (left-aligned, no container)
- Three dots with staggered opacity animation (0.2 → 0.6, 150ms offset each)
- Replaces itself with the actual response (fade transition, no layout shift)

---

## 5. Responsive Behavior

### 5.1 Breakpoints

```
Phone:     < 640px     Single column. Bottom sheet for panels. Full-width cards.
Tablet:    640-1024px  Single column with wider max-width (720px). Side panel as overlay.
Desktop:   > 1024px    Centered stream (760px max). Side panel pushes content.
```

### 5.2 Phone-Specific

- Composer sticks to bottom (above system gesture area)
- Cards use full viewport width minus 16px padding
- Bottom sheets use native-feeling drag physics
- Status strip sits below the safe area notch

### 5.3 Desktop-Specific

- Composer can be wider, centered with stream
- Side panel is a persistent option (toggle in header)
- Keyboard shortcuts: `Enter` to send, `Shift+Enter` for newline, `/` for commands, `Esc` to close panels

---

## 6. Accessibility

- All interactive elements: minimum 44×44px tap targets
- Focus indicators: 2px tint outline (visible on keyboard navigation only)
- Screen reader: semantic HTML, ARIA labels on all interactive elements
- Color contrast: primary text on background ≥ 7:1, secondary ≥ 4.5:1
- `prefers-reduced-motion`: all animation → instant opacity transitions
- `prefers-color-scheme: light`: defer to Phase 8 (dark-only for now)
- `prefers-contrast: more`: increase surface opacity, add visible borders

---

## 7. Assets

### 7.1 Icons

Minimal icon set. Feather-style (thin stroke, consistent weight).

| Icon | Usage |
|------|-------|
| Arrow right | Send button |
| Paperclip | Attachment |
| Chevron right | Expandable sections |
| X | Close panels, dismiss |
| Search | Memory search |
| Menu (hamburger) | Desktop side panel toggle |
| Feather (🪶) | Brand mark, empty state, favicon, loading |

Use inline SVG, not icon fonts. 20×20px default, 1.5px stroke.

### 7.2 App Icons

- 192×192 (standard)
- 512×512 (standard)  
- 512×512 maskable (safe zone: inner 80%)
- Design: the feather mark on dark background, minimal

---

## 8. CSS Architecture

### 8.1 File Structure

```
web/
  style.css          → Design tokens (custom properties) + reset + layout
  components.css     → Component styles (cards, composer, panels, status)
  motion.css         → All animations and transitions
  responsive.css     → Breakpoint overrides
```

### 8.2 Token Naming

All design tokens are CSS custom properties on `:root`:

```css
:root {
  /* Colors */
  --color-bg: #0a0f1e;
  --color-surface: rgba(255,255,255, 0.06);
  --color-surface-raised: rgba(255,255,255, 0.10);
  --color-surface-active: rgba(255,255,255, 0.14);
  --color-surface-border: rgba(255,255,255, 0.08);
  
  --color-text-primary: rgba(255,255,255, 0.92);
  --color-text-secondary: rgba(255,255,255, 0.55);
  --color-text-tertiary: rgba(255,255,255, 0.30);
  
  --color-tint: #7ecbff;
  --color-tint-hover: #9dd8ff;
  --color-tint-subtle: rgba(126,203,255, 0.12);
  --color-tint-glow: rgba(126,203,255, 0.06);
  
  --color-status-green: rgba(52,199,89, 0.85);
  --color-status-amber: rgba(255,204,0, 0.85);
  --color-status-red: rgba(255,69,58, 0.85);
  
  /* Typography */
  --font-sans: -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', system-ui, sans-serif;
  --font-mono: 'SF Mono', 'JetBrains Mono', 'Fira Code', monospace;
  
  --text-xs: 0.75rem;
  --text-sm: 0.8125rem;
  --text-base: 0.9375rem;
  --text-md: 1.0625rem;
  --text-lg: 1.25rem;
  --text-xl: 1.5rem;
  
  /* Spacing */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 12px;
  --space-lg: 16px;
  --space-xl: 24px;
  --space-2xl: 32px;
  --space-3xl: 48px;
  
  /* Radii */
  --radius-sm: 8px;
  --radius-md: 14px;
  --radius-lg: 20px;
  --radius-full: 9999px;
  
  /* Motion */
  --ease-spring: cubic-bezier(0.22, 1, 0.36, 1);
  --ease-out: cubic-bezier(0, 0, 0.2, 1);
  --duration-instant: 100ms;
  --duration-fast: 200ms;
  --duration-default: 300ms;
  --duration-slow: 400ms;
  --duration-deliberate: 600ms;
  
  /* Blur */
  --blur-surface: 40px;
  --blur-raised: 60px;
  --blur-overlay: 80px;
  
  /* Layout */
  --stream-max-width: 760px;
  --panel-width: 380px;
  --card-max-height: 300px;
  --composer-max-lines: 5;
}
```

---

## 9. Implementation Phases

### Phase A: Foundation (Current Sprint)
- [ ] CSS tokens and reset
- [ ] Stream layout (no bubbles)
- [ ] Composer redesign (minimal input)
- [ ] Empty state
- [ ] Connection status (dot only)
- [ ] Responsive breakpoints

### Phase B: Interaction
- [ ] Card component (glass surface)
- [ ] Expandable sections (▸ Details)
- [ ] Status strip
- [ ] Work panel (bottom sheet)
- [ ] Motion system
- [ ] History fade

### Phase C: Polish
- [ ] Side panel (desktop)
- [ ] Slash command palette
- [ ] Thinking indicator
- [ ] Keyboard shortcuts
- [ ] Accessibility audit
- [ ] `prefers-reduced-motion` support

---

*This spec is the source of truth for all Silas frontend work. Backend rendering (HTML generation for cards, responses) must produce markup that aligns with these components.*
