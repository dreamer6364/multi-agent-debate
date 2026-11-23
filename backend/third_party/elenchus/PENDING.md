# Pending work

State as of 2026-07-19 (post overnight session). Everything below is *not done*;
the done work is in `CHANGELOG.md` and the git log.

## Next up (was on the task board)

- **Popup: per-mode check selection.** Page scan arguably wants `--check all`
  (fallacy + rhetoric + bias + scam) while as-you-write wants just `fallacy`.
  Today one `check` setting drives all three modes. Add a "Page scan uses"
  select (`same as check` / `all`); thread a `scanCheck` setting through
  `content.js` → `analyze()` (mind the cache keys, which already include the
  check).
- **Cut 0.2.0.** Bump `pyproject.toml` + `chrome-extension/manifest.json`,
  retitle the `Unreleased` CHANGELOG section, tag. Everything is written and
  pushed; only the version ceremony is left.

## Known gaps and rough edges

- **Selection chip doesn't follow page scroll** — it's `position: fixed` at the
  selection's position at mouseup; scrolling leaves it behind until the next
  selection. Reposition on scroll or hide on scroll.
- **Live mode underlines have no hover tooltip** (page-scan underlines do).
  The findings are one badge-click away, but parity would be nicer.
- **Overlapping quotes in *rich editors*** each get their own Range (fine), but
  in **textareas** overlapping spans are merged into one segment — the merged
  underline can't distinguish two findings. Cosmetic.
- **Top frame only.** Editors and chats living inside iframes (some embeds,
  old forums) are invisible. Setting `all_frames: true` works but injects the
  whole UI per frame; needs a lighter frame-mode first.
- **Canvas editors unsupported** (Google Docs) — inherent to the approach.
- **Page-scan block collection is O(n²)** in candidates per collect pass
  (`includes` + `contains` dedup). Fine at HN scale (~1k blocks); a giant
  infinite-scroll page might feel it. Switch to a Set + parent-walk if needed.
- **`elenchus serve` is single-config** — changing `elenchus.yaml` requires a
  restart. A config-file watcher (or `/api/reload`) would be friendlier.
- **Popup error surface is minimal** — server-down shows in the status line and
  badge/pill error states, but there's no retry button.

## Testing debt

- The dev QA harness (`chrome-extension/dev/qa.html`) is manual-ish; the node
  suite covers only `lib.js`. A scripted Puppeteer/Playwright run over the QA
  page (chip → panel → scan → underline count) would make extension regressions
  visible without a human.
- Live-site injection testing is blocked by page CSPs and Chrome's Local
  Network Access gate (documented in `chrome-extension/README.md`); the
  saved-copy workflow (`curl` a page into `dev/`, serve from localhost) is the
  supported path. The packaged extension itself is unaffected.
- `elenchus serve` has no test for `ELENCHUS_TRUST_ORIGINS` or the
  `Access-Control-Allow-Private-Network` preflight header.

## Ideas (unscoped)

- **Auto-scan allowlist**: remember per-hostname "always scan this site"
  (e.g., discord.com) instead of a per-visit toggle.
- **Findings export**: copy/download the pill list as markdown — useful for
  moderation notes or debate receipts.
- **Debate mode preset in the popup** mirroring the config-builder presets.
- **Firefox port**: MV3 + `browser.*` shims; CSS Custom Highlight API shipped
  in FF 140; the mirror-div overlay is engine-agnostic.
- **Streaming/partial results** for big page scans (server returns per-check
  results as they land instead of after the full batch).
- **On-device pre-filter** — a cheap heuristic (exclamation density, absolutist
  phrases) to skip obviously-clean blocks before spending model tokens.

## Operational notes

- The Chrome extension is *loaded unpacked* from `chrome-extension/`; after
  pulling changes, hit ⟳ on `chrome://extensions` and reload target tabs.
- `chrome-extension/dev/hn-sample.html` (git-ignored) is a saved HN thread used
  for wild-DOM testing; refresh it with `curl` when wanted.
- GitHub Pages serves `main:/docs` at https://quetzaluz.github.io/elenchus/ —
  it updates on push, no action needed.
