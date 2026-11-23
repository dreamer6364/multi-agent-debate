# Changelog

## Unreleased

### Added
- **Chrome extension** (`chrome-extension/`, load unpacked) with three modes:
  - *Selection check* — highlight text → ⚖ chip or right-click → results panel
  - *As-you-write* — live re-analysis of the focused textarea/rich editor after a
    typing pause, with CSS Custom Highlight underlines and a findings badge
  - *Page scan* — per-page toggle that underlines flawed reasoning in page
    content and keeps watching for new messages (Discord/forums); hover for a
    tooltip, click for details, bottom-left pill lists all findings
- **`elenchus serve`** — localhost-only JSON API (`/api/health`, `/api/analyze`)
  over the shared engine, with an LRU result cache (cache hits skip the rate
  limiter) and an origin policy that refuses web-page callers
- Extension dev harness (`chrome-extension/dev/`) that runs the content script
  on a plain local page against a live server
- GitHub Pages docs site (`docs/`)
- `chrome-extension/build.sh` to zip the extension for another machine

### Fixed
- Quote→highlight mapping is whitespace/case/curly-quote tolerant and handles
  quotes that span block boundaries in page-scan batches
- Page scan skips hidden/nav/aria-hidden content and never analyzes both a
  container and its children
- `rate_limit_per_min: 0` in a config file no longer silently becomes 5;
  unknown `--check` names are rejected with a clear error
