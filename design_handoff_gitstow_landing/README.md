# Handoff: Gitstow Landing Page

## Overview
A full marketing landing page for **gitstow**, a git repository library manager (github.com/rishmadaan/gitstow). The page targets AI-assisted developers who maintain local clones of repos they learn from. Structure inspired by openclaw.ai and better-auth.com: ASCII-art hero, tabbed install, product dashboard showcase, AI-integration section, feature grid, competitor comparison, CTA, footer.

## About the Design Files
The files in this bundle are **design references created in HTML** — prototypes showing the intended look and behavior, not production code to ship directly. The task is to **recreate this design in the target environment**. For a static marketing site, a plain HTML/CSS page, Astro, or Next.js static export are all appropriate; if the gitstow repo grows a `site/` or `docs/` folder, match whatever tooling lives there. `Gitstow Landing.dc.html` is the design source (an HTML component format; the markup between `<x-dc>` tags plus the inline `class Component` script contains everything). `browser-window.jsx` is only the Chrome-window frame used around the dashboard mockup.

## Fidelity
**High-fidelity.** Colors, typography, spacing, and copy are final and are taken directly from gitstow's own product stylesheet (`src/gitstow/web/static/app.css` in the gitstow repo). Recreate pixel-perfectly.

## Design Tokens
All from gitstow's product brand (keep in sync with `app.css`):

Canvas: `--bg #0b0c0e`, panel `#121418`, surface `#17191d`, surface-hi `#1f2229`, border `#24272d`, border-hi `#32363d`, divider `#1c1f24`, alt section bg `#0d0e11`.
Text: primary `#eceef1`, soft `#bbc0c6`, muted `#787d85`, muted-soft `#545962`, dim `#3a3e46`.
Accent (ember): `#ff6b35`, hover `#ff8457`, ink-on-accent `#120500`, dim `rgba(255,107,53,0.16)`, glow `rgba(255,107,53,0.3)`.
Status: clean `#4ade80`, dirty `#fbbf24`, conflict `#f87171`, behind `#3b82f6`, ahead `#c084fc`, frozen `#6b7280`. Workspace hues: oss amber `#f59e0b`, active blue `#3b82f6`.
Radius: 3px (buttons/inputs/code), 4px (cards), 6px (window frames). Shadows: `0 20px 60px rgba(0,0,0,0.65), 0 4px 12px rgba(0,0,0,0.3)` on framed mockups.

Typography (variable fonts bundled in `assets/fonts/`):
- **Bricolage Grotesque** (variable, weight 300–700, width 75–100%) — all headings and UI text. Headings use `font-variation-settings: 'opsz' 96, 'wdth' 82, 'wght' 600`, letter-spacing −0.03em; section h2s use `'opsz' 48, 'wdth' 88, 'wght' 600`.
- **JetBrains Mono** (variable, 400–700) — ASCII art, commands, eyebrows, tags, metrics, all code.
Base: 15px / 1.6. Eyebrows: mono 12px uppercase, letter-spacing 0.14em, muted, with an 18×1px accent bar.

Page background: `radial-gradient(ellipse 1200px 600px at 50% -300px, rgba(255,107,53,0.07), transparent 60%)` over `#0b0c0e`. Selection: accent bg, `#120500` text. Links: accent, hover `#ff8457` + underline (3px offset).

## Screens / Views
One page, max content width 1260px (32px side padding), sections separated by 1px `#1c1f24` rules. Alternating section backgrounds: default / `#0d0e11`.

### 1. Sticky nav
Sticky top, `rgba(11,12,14,0.82)` + `backdrop-filter: blur(12px) saturate(160%)`, 1px bottom border. Left: 7px pulsing accent dot (2.2s box-shadow ring animation), "gitstow" wordmark (Bricolage 22px, wdth 85, wght 700), mono tagline "git library manager" behind a 1px left border. Right: uppercase mono-ish links (12px, letter-spacing 0.15em, muted → white on hover): Install, Dashboard, AI, Features, Compare (anchor links, smooth scroll), plus outlined "GitHub ↗" button (border `#32363d`; hover: accent border/text + `rgba(255,107,53,0.16)` bg).

### 2. Hero (centered)
Padding 96px top / 88px bottom. Eyebrow "a git repository library manager" flanked by two accent bars. ASCII-art "GITSTOW" in a `<pre>` (JetBrains Mono, `clamp(8px, 1.5vw, 16px)`, line-height 1.18, color accent, `text-shadow: 0 0 32px rgba(255,107,53,0.35)`, user-select none) — exact art is in the design file. H1 40px: "Clone it. Stow it. **Never lose it.**" (last phrase accent). Lede paragraph (17px, max 620px, contains inline `code` chip `~/misc-stuff-2`). CTA row: command bar (`#121418` bg, `#32363d` border, mono 15px: dim `$` + white `pipx install "gitstow[ui]"` + copy button) and solid accent button "Star on GitHub ↗" (hover: `#ff8457` + glow shadow). Below, mono 12px dim meta: `MIT · Python 3.10+ · GitHub / GitLab / Bitbucket / Codeberg`.

Copy button pattern (used 3×: hero, quick-start, final CTA): 1px `#32363d` border, mono 11px uppercase "COPY", hover accent; on click writes command to clipboard and shows "COPIED ✓" for 1.6s.

### 3. Quick start (`#install`)
Two columns (flex, 56px gap, wraps). Left (min 380px): eyebrow "quick start", h2 32px "Sixty seconds to a stowed library.", intro line. Tabs: `pipx (recommended)` / `pip` / `CLI only` — mono 12px buttons, top-rounded (3px 3px 0 0), inactive: transparent + muted; active: `#121418` bg, accent text, bottom border merged into the command panel below (−1px margin trick). Commands per tab: `pipx install "gitstow[ui]"` / `pip install "gitstow[ui]"` / `pip install gitstow`. Below: a 4-line terminal panel (mono 13.5px, line-height 2), each line `$` + command + dim comment: `gitstow onboard` (# optional first-run wizard, installs the Claude skill too), `gitstow add anthropic/claude-code` (# shorthand or full URLs), `gitstow pull` (# update everything, in parallel), `gitstow ui` (# open the dashboard ↓).
Right (min 420px): terminal-framed `assets/demo.gif` (900×500) — frame: `#121418`, 6px radius, header bar with three 11px traffic dots (`#f87171`/`#fbbf24`/`#4ade80` at 0.8 opacity) + mono label "gitstow — demo", large shadow. Caption below, centered mono 11.5px dim: "live capture: add, pull, status, done".

### 4. Dashboard showcase (`#dashboard`, bg `#0d0e11`)
Centered header: eyebrow "gitstow ui", h2 36px "The tab you leave open.", paragraph emphasizing local-first (`127.0.0.1` as code chip). Centerpiece: a Chrome-style browser window (1120×780, dark macOS chrome, URL `127.0.0.1:7853`) containing a **faithful recreation of gitstow's real dashboard** (source of truth: `web/templates/dashboard.html` + `app.css` in the gitstow repo):
- Product nav: pulse dot, "gitstow" 17px, mono "library.mgr · v0.2.6", tabs Library (active, accent + 2px underline) / Workspaces / Settings.
- Hero row: mono eyebrow "Jul 21 — Dashboard", 34px "Your **library**." (library in accent), subtitle "44 repos across 2 workspaces, 41 in sync."; right-aligned metrics strip (mono 11.5px): glowing 7px pips — 38 clean (green), 03 local changes (amber), 02 behind (blue), 01 frozen (gray).
- Action bar: fake select "All workspaces ▾", search field placeholder "Search repos, tags…", "Hide frozen" checkbox, then ↻ Refresh (ghost), ⟳ Fetch all (outline), + Add repo (outline), ↓ Pull all (solid accent).
- Ledger table, 9-column grid `28px 100px minmax(170px,1fr) 58px 58px 58px 88px 58px 84px`, 10px column gap; mono uppercase 9.5px headers (№ / Status / Repository / Workspace / Branch / Remote Δ / Tags / Last pull / Actions). Six rows (status dot + mono label colored per status; workspace label colored amber `oss` / blue `active`; bordered tag chips; ghost/accent/disabled Pull buttons; `⋯` menu glyph):
  01 clean anthropic/claude-code · oss · main · — · ai,tools · 2h ago · Pull(ghost)
  02 behind facebook/react · oss · main · ↓ 12 · ui · 3d ago · **↓ Pull 12** (solid accent)
  03 "2 mod · 1 unt" (dirty amber) karpathy/nanoGPT · oss · master · — · ai · 1d ago · Pull(ghost)
  04 ahead gitstow · active · main · ↑ 2 · active · 5h ago · Pull(ghost)
  05 frozen torvalds/linux · oss · master · — · reference · 3w ago · Pull(disabled, 0.3 opacity)
  06 clean ziglang/zig · oss · master · — · lang · 2h ago · Pull(ghost)
  Footnote: "auto-refresh 30s · click repo name → details · pull runs in place".
- Product footer: green glowing dot + "LIVE" + code chip `127.0.0.1:7853`, right "Shutdown".
Below the window, three centered blurbs (max 240px each): "local-first" / "pull all, in parallel" / "per-file diffs" — accent mono uppercase label + 13.5px body.

### 5. AI integration (`#ai`)
Two columns. Left: eyebrow "ai integration", h2 "Your agents just… know.", paragraph, then three numbered items (26px bordered square with accent mono digit): 1 Skill installs itself (part of `gitstow onboard`, auto-updates on version bumps) · 2 Zero token overhead (skills cost nothing when inactive, unlike MCP tools that squat in context) · 3 MCP for everything else (`pip install gitstow[mcp]`, every command speaks `--json`).
Right: terminal-framed "claude code" transcript mockup (mono 13px / 1.9): user prompt `› grab karpathy/nanoGPT and tag it ai` (accent `›`), dim tool lines `⏺ Using skill: gitstow`, `⏺ Bash(gitstow add karpathy/nanoGPT)`, green `✓ cloned → ~/oss/karpathy/nanoGPT`, `⏺ Bash(gitstow repo tag karpathy/nanoGPT ai)`, green `✓ tagged: ai`, assistant reply "Stowed. It's in your oss workspace under karpathy/nanoGPT, tagged [ai]." (oss in amber, ai as tag chip), then a prompt line with a blinking 8×15px accent block cursor.

### 6. Feature grid (`#features`, bg `#0d0e11`)
Centered header "Everything a library needs." Grid `repeat(auto-fit, minmax(272px, 1fr))`, 16px gap. Eight cards (`#121418` bg, `#24272d` border, 4px radius, 24px/22px padding; hover: lighter border + `#17191d` bg): accent mono glyph (⌂ ⇣ ⇊ ❄ ⎇ ⌕ ⇄ ❯), 17px Bricolage title, 13.5px muted body, footer mono 11px command in a dark code chip (single line, ellipsized). Titles/commands: Workspaces (`gitstow workspace add ~/oss --layout structured`), Auto-organization (`gitstow add anthropic/claude-code facebook/react`), Bulk operations (`gitstow pull --tag ai --exclude-tag stale`), Freeze & tags (`gitstow repo freeze torvalds/linux`), Any git host (`gitstow add https://gitlab.com/group/project`), Search & exec (`gitstow search "def main" --glob "*.py"`), Portable collections (`gitstow collection export -o my-repos.yaml`), Shell sugar (`eval "$(gitstow shell init)"`). Full body copy is in the design file.

### 7. Comparison (`#compare`, max 900px)
Header: eyebrow "vs the field", h2 "The best of both. Then some.", paragraph. Bordered table (4px radius): header row `#121418`, grid `1fr 110px 110px 110px`, mono uppercase 11px — capability / **gitstow** (accent) / ghq / gita. Seven rows, glyphs mono (● accent for gitstow, dim `#545962` for others): Auto-organize by owner/repo ●●— · Bulk pull fetch & status ●—● · Workspaces ●—— · Tags freeze & filtered ops ●—◐ · Local browser dashboard ●—— · Claude Code skill + MCP ●—— · JSON output everywhere ●—◐. Legend below, mono 11px dim: "● yes · ◐ partial · — no  (ghq 3.5k★, gita 1.8k★, both great at what they do)".

### 8. Final CTA
Centered, 96px padding, bottom radial accent glow. H2 44px: "Stop losing repos. / Start **stowing** them." Sub: "Your future self, and your agents, will thank you." Command bar with copy button (same pattern, 16px mono).

### 9. Footer
Top block: brand (dot + wordmark + "A git repository library manager, built for the age of AI-assisted development.") and two link columns (mono uppercase 10.5px column labels; 13.5px muted links, accent on hover): **Docs** → getting-started / commands / concepts / configuration (GitHub blob URLs), **Project** → GitHub, PyPI, Changelog, Contributing. Bottom bar behind 1px divider, mono 11.5px dim: "© 2026 · MIT license" left, "clone responsibly." right.

## Interactions & Behavior
- Anchor nav with `scroll-behavior: smooth`.
- Install tabs: single state var (`pipx` | `pip` | `cli`) swaps the command string and active-tab styling.
- Copy buttons: `navigator.clipboard.writeText(cmd)`, label → "copied ✓" for 1.6s (per-button timers).
- Hovers: nav links muted→white; buttons per token spec above; feature cards border/bg lift.
- Pulse animation (nav dot, cursor): `box-shadow 0 0 0 0 rgba(255,107,53,0.3)` → `0 0 0 6px transparent`, 2.2s ease-in-out infinite.
- Dashboard mockup is static (non-interactive imagery); demo.gif autoplays.
- Responsive: columns wrap via flex min-widths; ASCII scales with clamp; ledger's Repository column has minmax(170px, 1fr) so it never collapses.

## State Management
Client-only: `tab` (install tab), three transient `copied*` booleans. No data fetching.

## Assets
- `assets/demo.gif` — real CLI capture from the gitstow repo (900×500).
- `assets/fonts/*.woff2` — Bricolage Grotesque + JetBrains Mono variable fonts (latin), copied from `src/gitstow/web/static/fonts/` in the gitstow repo (Google Fonts, OFL). Latin-ext subsets exist in the repo if needed.
- No other imagery; the dashboard "screenshot" is coded, so it stays crisp. Optionally replace it with a real product screenshot.

## Screenshots
Reference captures of the rendered design are in `screenshots/` (viewport-width sections, top to bottom): 01-hero, 02-quick-start, 03-dashboard-showcase, 04-ai-integration, 05-feature-grid, 06-comparison, 07-cta-footer. The HTML file remains the source of truth for exact values.

## Files
- `Gitstow Landing.dc.html` — the full design: all markup, inline styles, copy, ASCII art, and the logic class (tabs/copy/data arrays for rows, features, comparison).
- `browser-window.jsx` — Chrome window frame component used around the dashboard mockup (reference for recreating the frame).
- `assets/` — gif + fonts as above.
- `screenshots/` — rendered reference captures.
