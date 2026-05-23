# Handoff: gitmem TUI

## Overview

A terminal user interface (TUI) for **gitmem** — a git-native memory system for AI CLI agents. The TUI is an all-in-one cockpit that lets a user audit what their agents "know," watch live sessions, review dream-pipeline PRs, and resolve conflicts.

The headline design move: **the landing page tells you in one glance whether you need to do anything.** Big green ✓ = close the terminal. Big red ✗ = action items below, each one keypress away from resolution.

## Screenshots

See `screenshots/` for PNG reference renders of each screen:

- `01-overview-good-day.png` — F2 Overview, all green
- `02-overview-bad-day.png` — F2 Overview, action required
- `03-facts.png` — F3 Facts
- `04-sessions.png` — F4 Sessions
- `05-dream.png` — F5 Dream
- `06-prs.png` — F6 PRs

## About the Design Files

The files in this bundle are **design references created in HTML** — interactive prototypes showing intended look and behavior, **not production code to copy directly**. Your task is to **recreate these designs in the target codebase's environment**.

Target environment is a real terminal. Recommended stack:
- **Rust + [ratatui](https://github.com/ratatui-org/ratatui)** — best-in-class Rust TUI lib, matches the box-drawing/sparkline aesthetic
- **Go + [bubbletea](https://github.com/charmbracelet/bubbletea)** + [lipgloss](https://github.com/charmbracelet/lipgloss) — equally good, simpler if the team is Go-leaning
- **Python + [textual](https://github.com/Textualize/textual)** — viable but heavier; prefer Rust/Go for a CLI tool

Pick whichever fits the gitmem core repo's language. The HTML mocks use real Unicode box-drawing characters (┌─┐│└┘├┤ etc.), block elements (█▌░), and braille sparkline glyphs (▁▂▃▄▅▆▇█) — all of which render natively in any modern terminal.

## Fidelity

**Mid-to-high fidelity wireframes.** Colors, typography, spacing, glyphs, and copy are final and should be reproduced exactly. Layout proportions are intentional but flexible — adapt to actual terminal width (the designs target 80–120 cols with graceful degradation).

## Screens / Views

### F2 · Overview (landing)

**Purpose:** Show overall memory health in one glance. Good days are inbox-zero; bad days surface actionable items.

**Layout (top to bottom):**
1. Terminal chrome (mock only — drop in real implementation)
2. **Primary nav bar** (persistent on every screen)
3. **Sub-nav bar** (context bar for this view)
4. **Health hero strip** (single line, ~28px tall)
5. Status strip (version, mode, repo links, totals)
6. **Quad grid** of four panels — 2×2 layout

**Health hero (single line):**
- Left: colored glyph (✓ green / ◐ yellow / ✗ red) with text-shadow glow
- Title: `ALL GREEN` / `NEEDS ATTENTION` / `ACTION REQUIRED`
- Subtitle: dimmed plain-English status
- Right: inline `Health` pills for conflicts and pending PRs
- Background: matches state (`#0a1f0e` / `#1f1a08` / `#1f0a0a`)

**Quad grid panels:**
- **Memory** — strength histogram (S:1 to S:5) with horizontal bars + top scope link
- **Dream pipeline** — last run status, merged/pending/conflict counts, next auto-run countdown, throughput sparkline
- **Sessions** — top 5 recent sessions with status dot, tool name, age, summary; below: per-tool activity sparklines (24h)
- **What needs you** — when good day: "inbox zero" + recent merges; when bad day: list of every conflict/PR with one-keypress resolution

### F3 · Facts

**Purpose:** Browse and audit individual facts.

**Layout:** Three columns — `200px filter rail | 340px list | 1fr detail`

- **Filter rail:** scope checkboxes, strength min/max, source checkboxes, match count
- **List:** strength badge (`▮N` colored), title, scope+age, conflict flag if any. Selected row uses `bg-sel-active`.
- **Detail:** fact ID + strength + scope link header → fact text in green-bordered card → composite score bars (trust/relevance/retention) → provenance frame → git history frame with commit-hash links

**Sub-nav:** `all (1,284) | conflicted (3, red) | tombstoned (14) | superseded (28) | procedures (6, cyan)`

### F4 · Sessions

**Purpose:** Watch extraction happen and see what each session contributed.

**Layout:** Three columns — `280px session list | 1fr transcript | 320px contributions`

- **Session list:** grouped by date, each entry shows status dot, tool, age, summary, mini sparkline
- **Transcript:** time-coded log with `user:` / `tool:` / agent name colored. **Extraction markers** appear inline as green-bordered cards (or yellow for low-confidence) showing the proposed fact and source
- **Contributions:** proposed facts → links to PR, retrieved (injected) facts, stats, "on session end" action box

**Sub-nav:** `all (47) | codex (14) | claude (22) | copilot (7) | gemini (3) | opencode (1) | live (1, yellow)` — clicking a tool filters the list and unifies the transcript scope

### F5 · Dream

**Purpose:** Live pipeline view + run control.

**Layout:** Two columns above a horizontal info strip.

- Left: pipeline stages (orient/gather/consolidate/lint/prune) each as a progress bar with status glyph
- Left below: throughput sparkline
- Right: results so far (proposed/merged/deduped/conflicts as Health pills with counts), opened PRs (linked), sessions consumed (linked)
- Bottom strip: next auto-run countdown, last 7 runs sparkline, `[ d  run now ]` button

**Sub-nav:** `live | history (18) | schedule | config`

### F6 · PRs

**Purpose:** Review and merge dream-pipeline PRs; resolve conflicts.

**Layout:** Two columns — `1fr diff/notes | 420px conflict resolver`

- Left: PR header (number, label tag, title, github link), opener/reviewer line, diff body with +/±/⚑ markers, reviewer notes in blue-bordered card
- Right: conflict resolver — title, then 4 option cards (A/B/C/D), selected option uses cyan border + dark-cyan bg, then green-bordered "apply & merge" action button

**Sub-nav:** `pending (5, yellow) | conflicts (3, red) | merged (128, green) | closed (12) | all (148)`. Right-aligned: `github.com/<org>/memory/pulls ↗` direct link.

### F7 · Search (not mocked yet)

FTS5 across facts + sessions + transcripts. Same three-column shape as Facts.

## Interactions & Behavior

### Global keybindings
- `F2`–`F7` → switch primary nav
- `↑↓` → navigate list within active pane
- `←→` → switch panes within a screen
- `↵` → open/expand selected item
- `/` → focus filter input
- `Esc` → back / close modal
- `q` → quit
- Page-specific: `c` (conflict resolver), `d` (run dream), `t` (tombstone), `i` (inject preview), `e` (edit), `o` (open on GitHub), `f` (open file in editor), `r` (replay session), `a`/`b`/`c`/`d` (pick option in resolver), `x` (reject PR)

### Active-tab visual
Primary nav active tab: inverse colors (`bg: var(--term-fg); color: var(--term-bg)`). Sub-nav active: white text + underline.

### Links
Two link styles, distinguishable by trailing glyph:
- **File / scope / session links** → `↗` (open in editor) — color `var(--ansi-cyan)`
- **GitHub / PR / commit links** → `⎋` (open on GitHub) — color `var(--ansi-cyan)`, PR numbers themselves use `var(--ansi-yellow)`

Underline: dotted, offset 2px, color `var(--term-line-bright)`.

### Live updates
- Sparklines and progress bars should update on a 500ms tick (btop-style)
- Session list: live sessions get `◉` yellow glyph; finished get `●` green; older get dim `○`
- Extraction markers in transcripts appear as new ones are emitted by the live extractor

### Health pill states
- `ok` → green `●`
- `warn` → yellow `◐`
- `bad` → red `✗`
- `idle` → dim `○`

## State Management

Minimum state to track:
- Current primary nav (F2–F7)
- Current sub-nav within each page
- Selected list item per page
- Filter state per page (filter rail values)
- Live session stream subscription
- Live dream-pipeline stage progress
- Conflict resolver selection (current PR's chosen option A/B/C/D)
- Page-level navigation stack (for `Esc` back)

Data sources (gitmem core):
- Local SQLite (FTS5) for facts, sessions, scopes
- Git history for fact change log
- GitHub API (via local PAT) for PR status
- Long-poll or filesystem-watch the dream pipeline run dir for live stage updates

## Design Tokens

### Colors
```
--term-bg          #0b0e14   /* primary background */
--term-bg-alt      #10141c   /* sub-nav background */
--term-bg-panel    #0d1117   /* nav bar background */
--term-fg          #c9d1d9   /* primary text */
--term-fg-bright   #f0f6fc   /* emphasized text / titles */
--term-fg-dim      #6e7681   /* secondary text */
--term-fg-faint    #484f58   /* tertiary / disabled */
--term-line        #30363d   /* borders */
--term-line-bright #484f58   /* emphasized borders */

ANSI palette:
--ansi-red         #f85149
--ansi-green       #3fb950
--ansi-yellow      #d29922
--ansi-blue        #58a6ff
--ansi-magenta     #bc8cff
--ansi-cyan        #39c5cf
--ansi-orange      #ff8c42

Strength scale (1=weak → 5=ground truth):
--s1 #f85149 (red)
--s2 #ff8c42 (orange)
--s3 #d29922 (yellow)
--s4 #3fb950 (green)
--s5 #56d364 (bright green, +glow)

Hero backgrounds:
ok    #0a1f0e
warn  #1f1a08
bad   #1f0a0a
```

### Typography
- Family: terminal default monospace. In the mocks: JetBrains Mono fallback to Menlo/Consolas/ui-monospace
- Sizes: 13px body, 12px nav/sub-nav, 11px chrome
- Weights: 400 normal, 500 medium, 700 bold for titles and emphasized counts
- Line-height: 18px body (1.38)

### Glyphs
| Glyph | Meaning |
| --- | --- |
| `●` | live / ok |
| `◉` | running / in-progress |
| `○` | idle / done |
| `✓` | complete / approved |
| `✗` | conflict / blocker |
| `◐` | warn / partial |
| `⚑` | conflict needing human |
| `△` | updated |
| `+` | added |
| `±` | updated value |
| `▮` | strength badge cell |
| `↗` | open file/local link |
| `⎋` | open external (GitHub) |
| `▁▂▃▄▅▆▇█` | sparkline scale |
| `█▌░` | progress bar fill |
| `┌─┐│└┘├┤┬┴┼` | single box |
| `╔═╗║╚╝` | double box (reserved for emphasis) |

### Spacing
- Cell width: 1 character (~8.4px at 13px JetBrains Mono)
- Panel padding: 10px 14px
- Nav bar height: ~28px; sub-nav: ~24px; hero: ~30px
- Grid gap between panels: 1px (just border showing through)

## Assets
No images. All visuals are Unicode characters + CSS color. Fonts loaded from Google Fonts in the mock (JetBrains Mono); use the terminal's configured monospace in production.

## Files

Design source (in this bundle):
- `gitmem-tui-wireframes.html` — main entry, wraps everything in a design canvas
- `styles.css` — design tokens and shared classes
- `nav.jsx` — `NavBar`, `SubNav`, `Link`, `Health` components (the patterns to port)
- `term.jsx` — `Frame`, `Strength`, `Bar`, `Spark`, `Hints` (the visual primitives)
- `wire1-overview-v2.jsx` — **Overview / landing page** (the canonical reference)
- `wire-v2-screens.jsx` — Facts, Sessions, Dream, PRs drill-in screens
- `wire1-cockpit.jsx`, `wire2-fact-detail.jsx`, `wire3-sessions.jsx`, `wire4-pr-review.jsx` — earlier v1 explorations (skip)
- `storyboards.jsx` — 4-frame flow storyboards: review PR, resolve conflict, run dream

Open `gitmem-tui-wireframes.html` in a browser to see all screens side-by-side in a pan/zoom canvas.

## Build order suggestion

1. **Skeleton + nav** — `NavBar` + `SubNav` + page-switch state, all six pages empty
2. **Overview (F2)** — health hero + quad grid (read static fixtures first, wire to real data after)
3. **Facts (F3)** — list + detail; this exercises the most data-binding patterns
4. **Sessions (F4)** — needs the live transcript subscription
5. **Dream (F5)** — needs the live pipeline progress stream
6. **PRs (F6)** — needs GitHub API + the conflict resolver state machine
7. **Search (F7)** — wraps FTS5; reuse Facts layout
