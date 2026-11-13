---
name: Conclave Studio
description: A light plotting board for directing a team of agents, with a navy rail and blue reserved for action and work.
colors:
  deck: "#eef2f6"
  panel: "#ffffff"
  panel-sunk: "#e6edf5"
  line: "#cfd8e3"
  line-strong: "#9fb3c8"
  ink: "#0f1a26"
  ink-muted: "#4a5a6c"
  ink-dim: "#52637a"
  action-blue: "#1d5fb8"
  action-blue-deep: "#164c94"
  on-action: "#ffffff"
  success: "#1d7a42"
  success-surface: "#e6f4ea"
  info: "#1f4f8f"
  info-surface: "#e8f0fb"
  idle-surface: "#edf1f5"
  working-surface: "#e5effa"
  warning-surface: "#fff3dc"
  error-surface: "#fbe9e7"
  person-amber: "#8a5300"
  person-amber-edge: "#e9c98a"
  person-paper: "#fffaf0"
  decision-ochre: "#b86a00"
  decision-ochre-deep: "#9a5800"
  failure-red: "#b3261e"
  shell-navy: "#0f1f33"
  shell-navy-raised: "#17304d"
  shell-navy-active: "#20406a"
  shell-text: "#eaf1f8"
  shell-muted: "#9fb4cc"
  shell-key: "#8fc3ff"
  you-mustard: "#f2a33a"
typography:
  headline:
    fontFamily: "Jost, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "0"
  headline-app:
    fontFamily: "Jost, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "0"
  title:
    fontFamily: "Jost, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 500
    letterSpacing: "0"
  card-title:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.25
  body:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.55
  body-ui:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  fact:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.3
  label:
    fontFamily: "Jost, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    letterSpacing: "0.08em"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 400
rounded:
  tag: "4px"
  control: "6px"
  card: "8px"
  plate: "10px"
  surface: "12px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  canvas-grid: "24px"
  node-gap: "60px"
  row-gap: "110px"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    textColor: "{colors.on-action}"
    typography: "{typography.body-ui}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-primary-hover:
    backgroundColor: "{colors.action-blue-deep}"
    textColor: "{colors.on-action}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body-ui}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-secondary-hover:
    backgroundColor: "{colors.panel-sunk}"
  button-decision:
    backgroundColor: "{colors.decision-ochre}"
    textColor: "{colors.on-action}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-decision-hover:
    backgroundColor: "{colors.decision-ochre-deep}"
  input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body-ui}"
    rounded: "{rounded.tag}"
    padding: "9px 11px"
  tag-ai:
    backgroundColor: "{colors.panel-sunk}"
    textColor: "{colors.action-blue}"
    typography: "{typography.label}"
    rounded: "{rounded.tag}"
    padding: "3px 8px"
  tag-human:
    backgroundColor: "{colors.warning-surface}"
    textColor: "{colors.person-amber}"
    typography: "{typography.label}"
    rounded: "{rounded.tag}"
    padding: "3px 8px"
  step-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "16px"
    width: "350px"
    height: "240px"
  step-card-human:
    backgroundColor: "{colors.person-paper}"
  member-mark:
    backgroundColor: "{colors.panel-sunk}"
    textColor: "{colors.action-blue}"
    rounded: "7px"
    size: "34px"
  canvas-dock:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.surface}"
    padding: "8px"
  rail:
    backgroundColor: "{colors.shell-navy}"
    textColor: "{colors.shell-text}"
    width: "60px"
  rail-item-active:
    backgroundColor: "{colors.shell-navy-active}"
    textColor: "#ffffff"
  poll-entry:
    backgroundColor: "{colors.shell-navy-raised}"
    textColor: "{colors.shell-text}"
    rounded: "{rounded.card}"
    padding: "10px 12px"
  poll-entry-you:
    backgroundColor: "{colors.you-mustard}"
    textColor: "{colors.shell-navy}"
  decision-plate:
    backgroundColor: "{colors.warning-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.plate}"
    padding: "16px 18px 18px"
---

# Design System: Conclave Studio

## Overview

**Creative North Star: "The Plotting Board"**

Studio is the table a builder spreads a plan across and then watches the work move over. The deck is a cool, light grey-blue field ruled with a faint 24px grid; on it sit white step cards joined by arrowed connections, and the run plays out on those same cards. A navy rail runs down the left edge as the room's wall: navigation, not a view. Everything else is daylight.

The register is grown-up and operational. Studio is trusted with real commits and merges, so it reads like a well-made instrument rather than a toy: a quiet sans for reading, a geometric sans (Jost) for names and labels, a monospace only where something is literally code or a backend identifier. Members appear under their own names with an AI or Human tag and a single-letter mark; there are no characters, franchises or mascots anywhere in the system.

Colour carries meaning, not decoration. Blue is for action and for work in progress. Amber is kept for the one thing that needs the person using Studio. Green, red and grey state outcomes. Depth is a whisper of navy-tinted shadow under cards, with stronger lift reserved for things that float over the board (the dock, the zoom control, dialogs).

**Key Characteristics:**
- Light deck, white cards, one navy rail.
- The canvas is the single view of a plan and its runs.
- Blue means act or working; amber means the person is needed.
- Jost for names and labels, Public Sans for reading, JetBrains Mono for code; all self-hosted WOFF2.
- Fixed-size step cards (350 x 240) that never clip their title.

## Colors

A cool daylight neutral field with one confident blue, a navy frame, and a narrow amber band reserved for the person.

### Primary
- **Action Blue** (`action-blue`): every primary button, links, the focus ring (2px outline, 3px offset), the caret, a selected card's border, a working step's border and glow, produced outputs, the AI tag. Deepens to **Action Blue Deep** (`action-blue-deep`) on hover. In code the custom property is still named `--mint`.

### Secondary
- **Person Amber** (`person-amber`): text and marks for what waits on the person: the Human tag, a step that needs input, stale run steps, delegation edges (dashed 5/4), validation notes. Its companions are **Amber Edge** (`person-amber-edge`) for borders, **Person Paper** (`person-paper`) for a human step card's fill, and the `warning-surface` wash.
- **Decision Ochre** (`decision-ochre`, hover `decision-ochre-deep`): only the primary button inside the decision plate, where the person signs off.

### Tertiary
- **Shell Navy** family (`shell-navy`, `shell-navy-raised`, `shell-navy-active`, with `shell-text`, `shell-muted`, and `shell-key` for the brand mark and the active application mark): the rail, the team status poll, the notice toast, and the pressed workspace-mode button. Never a full page background.
- **You Mustard** (`you-mustard`): the final "You / Your decision" entry of the team status poll and the rail's focus outline.

### Neutral
- **Deck** (`deck`): page and canvas background, under a 24px grid of `#cfd8e366` lines.
- **Panel** (`panel`): cards, dialogs, dock, inputs.
- **Panel Sunk** (`panel-sunk`): hover fills, tag backgrounds, member marks, collapsed groups.
- **Line** (`line`) and **Line Strong** (`line-strong`): hairline borders; the strong value for hover borders, run edges and search matches.
- **Ink** (`ink`), **Ink Muted** (`ink-muted`), **Ink Dim** (`ink-dim`): text, secondary text, labels and idle state.
- Status pairs: `success`/`success-surface`, `info`/`info-surface`, `idle-surface`, `working-surface`, `warning-surface`, `failure-red`/`error-surface`.

### Named Rules
**The Amber Is For You Rule.** Amber and its ochre and mustard relatives mark only what is waiting on the person: a Human step, a needed input, a stale step, the decision. A colour that means "the person is needed" is never used for "working".

**The Blue Does The Work Rule.** One blue does action and progress. There is no second accent hue for buttons, links or selection.

## Typography

**Display Font:** Jost (with system-ui, sans-serif)
**Body Font:** Public Sans (with system-ui, sans-serif)
**Label/Mono Font:** JetBrains Mono (with ui-monospace, monospace)

**Character:** Jost's geometric shapes give names and labels a precise, engineered voice; Public Sans keeps long reading plain and civic. All three are variable WOFF2 files served from `studio/web/fonts/` under the SIL Open Font License, with `font-display: swap`.

### Hierarchy
- **Headline** (Jost 500, 28px, 1.3): page headings; the application header sets 26px at 1.2.
- **Title** (Jost 500, 20px): dialog and section headings; h3 at 16px.
- **Card title** (Public Sans 700, 17px, 1.25): a step card's name, clamped to two lines and never cut by the facts below it.
- **Body** (Public Sans 400, 16px, 1.55): reading text; prose capped at 70ch. Dense UI text runs at 14px/1.5, the most common size in the build.
- **Fact** (Public Sans 400, 13px, 1.3): step facts (Produces, Member, Runs on); fact terms sit at 10px uppercase with 0.04em tracking in Ink Dim.
- **Label** (Jost 500, 11px, 0.08em, uppercase): AI/Human tags, permission tags and run-status words (0.06em). Save state and nav meta use the same face without uppercase.
- **Mono** (JetBrains Mono, 12px): backend identifiers, code, record IDs, the zoom level. Numbers in tables, timestamps and status counters use tabular figures.

### Named Rules
**The Names In Jost Rule.** Headings, tags and status words are Jost; anything a person reads at length is Public Sans; only literal code and identifiers are mono.

## Layout

A two-column shell: a 60px collapsed navy rail (232px when opened, labels hidden rather than clipped when closed) beside a fluid main area. Below 760px the rail becomes a top bar.

The canvas fills the main area. Step cards are fixed at 350 x 240 with 60px gaps between columns and rows; dependencies flow left to right, from a card's right edge to the next card's left. A long chain wraps into rows like text, at the row length that gives the whole plan the largest fitted zoom (never above 1), with 110px between rows; an edge to a step behind its source runs along the gap between rows with 24px-radius corners rather than across cards. A short plan is centred so it looks placed. The fitted plan sits above the floating dock. On a phone the plan reads as one column fitted to the screen width from the first step. Saved positions belong to the user and are rearranged only by Tidy.

Spacing works in small steps (6, 8, 12, 16px) inside controls and cards, with 24px+ between sections. Breakpoints observed: 1600px (wider grids), 1200px, 1000px, 900px, 760px (rail to top bar, single-column grids), 380px (tight side padding).

## Elevation & Depth

Mostly tonal, with soft navy-ink shadows. Cards rest almost flat; things that float above the board (dock, zoom control, notes, dialogs, the toast) carry a broader, softer lift. Selection and progress use rings, not heavier shadows. Shadow ink is the shell navy at low alpha (`rgba(15,31,51,…)`). The dock, zoom control, edge notes, toast and dialogs still carry a leftover teal ink (`#0f1f33`) from an older theme; that is drift, not part of the system.

### Shadow Vocabulary
- **Card rest** (`box-shadow: 0 2px 6px rgba(15,31,51,.06)`): step cards at rest.
- **Card selected** (`box-shadow: 0 0 0 1px var(--action) inset, 0 8px 20px rgba(15,31,51,.12)`): the selected step card, with a blue border.
- **Primary lift** (`box-shadow: 0 4px 12px rgba(15,31,51,.18)`): primary buttons.
- **Working pulse** (`0 0 0 2px #1d5fb822` to `0 0 0 7px #1d5fb814`, 1.6s ease-in-out, infinite): a step that is working now; disabled under reduced motion.
- **Floating** (`0 4px 14px`, `0 6px 24px`, `0 8px 24px` at low alpha): zoom control, dock, edge notes.
- **Dialog** (`0 24px 60px` at low alpha): dialogs and the notice toast.

### Named Rules
**The Rings Not Weight Rule.** State is shown with a border colour and an inset or outer ring; a card never gets heavier just because it is selected or busy.

## Shapes

Gently rounded, scaled by role: tags and inputs 4px, buttons and dock actions 6px, step cards, poll entries and the zoom control 8px, the team poll and decision plate 10px (joined: the poll keeps its top corners, the plate its bottom ones), the dock, dialogs and checkpoint panels 12px, groups 14px. Member marks are 34px rounded squares (7px). Borders are 1px hairlines; run steps take a 2px status border, dashed while waiting, ready or incomplete. Canvas groups are dashed boxes behind their steps. Connections are 2px strokes with a filled triangular arrowhead; selected connections thicken to 3px in blue.

## Components

### Buttons
Plain and confident; the primary carries a soft lift.
- **Shape:** gently rounded (6px), 1px border, 14px weight 550 text, 10px 14px padding.
- **Primary:** Action Blue fill, white text, Primary lift shadow.
- **Hover / Focus:** primary deepens to Action Blue Deep; secondary fills Panel Sunk with a blue border; every focusable shows a 2px blue outline offset 3px (mustard in the rail).
- **Secondary:** transparent with a Line border and Ink text. **Text button:** blue underlined link with a 4px underline offset. **Icon button:** borderless, Ink Dim, 36px hit area on small screens, inline SVG at 18px with a 1.7 stroke.
- **Decision:** inside the decision plate only, Decision Ochre fill with white text.

### Chips (tags)
- **Style:** AI tag in blue on Panel Sunk; Human tag in Person Amber on the warning surface with an Amber Edge border; Jost 11px uppercase, 4px radius.
- **State:** a saved test example shows a pill (999px) that turns red when outdated.

### Cards / Containers (the step card)
- **Corner Style:** 8px.
- **Background:** Panel; a Human step uses Person Paper with an Amber Edge border.
- **Shadow Strategy:** Card rest; Card selected when selected (see Elevation).
- **Border:** 1px Line; blue when selected, when on the selected chain, or when matched by search (non-matching cards drop to 40% opacity).
- **Internal Padding:** 16px. Read top to bottom: AI/Human tag; two-line title; facts grid (64px term column) for Produces, Member (a letter mark and the name) and Runs on (mono).

### Run step status
The run is drawn on the same cards. Status sets a 2px border and a fill: Working (blue border, working surface, pulse), Complete (green on success surface), Failed (red on error surface), Needs input (amber border with an amber inset ring on the warning surface), Waiting/Ready/Incomplete (dashed idle grey). The status word sits in uppercase Jost 11px beside a 15px marker in the status colour: a drawn tick for Complete, a cross for Failed, an exclamation for Needs input or a stale step, and an empty ring otherwise.

### Inputs / Fields
- **Style:** white fill, 1px Line border, 4px radius, 9px 11px padding, 14px text (16px on small screens to avoid zoom); selects use an inline SVG chevron.
- **Focus:** 2px blue outline, 3px offset; blue caret.
- **Error / Disabled:** errors in Failure Red at 14px; disabled text in Ink Dim; disabled buttons at 45% opacity.

### Navigation
- **Style:** the navy rail. Items are Shell Text; hover fills Shell Navy Raised; the active item fills Shell Navy Active in white. Each application has a 26px rounded mark with its letter in Jost, inverted to Shell Key on the active item.
- **Mobile:** below 760px the rail becomes a top bar and its footer is hidden.

### Canvas dock and zoom
A floating white bar centred 16px above the board's bottom edge (12px radius, 8px padding, hairline border), holding borderless 6px-radius actions that fill Panel Sunk with blue text on hover, split by 1px separators. A smaller zoom control (8px radius) shows the level in mono. On narrow screens the dock moves right and scrolls horizontally.

### Team status poll and decision plate
Before the person decides, the run record is read into a navy strip: one Shell Navy Raised entry per AI member with its name and a call (Done in green `#9adbb0`, Stopped in coral `#f5a593`, Still working in Shell Muted), ending in a You Mustard entry reading "Your decision". Entries rise in with a 90ms stagger (0.55s, `cubic-bezier(.16,1,.3,1)`), off under reduced motion. Directly beneath, joined to it, the decision plate on the warning surface with an Amber Edge border holds the task and the Decision button. On phones the poll becomes a two-column grid.

## Do's and Don'ts

### Do:
- **Do** keep the deck light and put navy only on the rail, the team poll, the toast and the pressed workspace mode.
- **Do** use Action Blue for every primary action, link, focus ring and working state.
- **Do** reserve amber, ochre and mustard for what waits on the person.
- **Do** show members under their own names with an AI or Human tag and a single-letter mark.
- **Do** keep step cards at 350 x 240 and let facts give up room before the two-line title does.
- **Do** draw shadows in navy ink (`rgba(15,31,51,…)`) and show state with rings and borders.
- **Do** honour `prefers-reduced-motion`: the pulse, the poll stagger and transitions all switch off.

### Don't:
- **Don't** use characters, franchises or mascots, or cast members as personas; Studio is grown-up, not cute.
- **Don't** introduce a second action hue, or use amber to mean "working".
- **Don't** add uppercase kicker labels above headings ("STEP 3", "APPLICATION SETUP"); the heading says it.
- **Don't** use text glyphs as status icons; markers are shapes or inline SVG.
- **Don't** reach for the old teal (`#0f1f33`, `#1e5a55`) or green values when adding shadows, edges or tags.
