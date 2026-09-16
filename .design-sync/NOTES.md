# design-sync notes — StockSmith UI

Target: claude.ai/design project **StockSmith UI** (`f0e6eb34-35ee-4131-9a0a-3f9aac2cb7eb`), first synced 2026-09-16.

## Shape and build

- StockSmith is an app, not a library. The sync runs off a tiny sub-package at `frontend/design-system/`
  (barrel `index.ts` + declaration-only `tsconfig.json` + `build-css.mjs`). `cfg.entry` points at the
  barrel source (esbuild bundles TS directly); `types` in its `package.json` points at the tsc output so
  the converter gets real `<Name>Props` bodies. Without this the converter falls into synth-entry mode
  and every `.d.ts` is an empty stub.
- **Scope = the barrel.** Adding a component to the sync means adding its export to
  `frontend/design-system/index.ts` (and a `docsMap` line for its group). Only standalone-renderable
  pieces belong there — anything that reads react-query/router/API context can't render in a canvas.
- `cfg.buildCmd` (`cd frontend/design-system && npm run build`) must run before the converter whenever
  component source OR a preview changed: `build-css.mjs` compiles Tailwind v4 from `src/index.css` by
  scanning `frontend/src/**` **and `.design-sync/previews/**`**. A class used only in a preview and not
  in the app exists in the sheet only because of that scan. `preview-rebuild.mjs` does NOT refresh
  `_ds_bundle.css` — a preview that introduces a new class needs the full `buildCmd` + `package-build`.
- Converter deps + Playwright live in `.ds-sync/` (gitignored); Chromium is in
  `%LOCALAPPDATA%\ms-playwright`. Re-run `npm i esbuild ts-morph @types/react playwright` there on a
  fresh clone.
- Node 24 / npm 11 were used; the repo pins nothing.

## Config decisions

- `docsMap` enumerates every component on purpose: the app has no per-component docs, and the map is
  the only way to put components into named groups (frontmatter-only stubs in `.design-sync/groups/`).
  The stubs have an empty body, so `.prompt.md` is still synthesized from JSDoc + previews.
- `dtsPropsFor` overrides six components whose auto-extracted body referenced types the emitter
  didn't inline (`TabDef`, `FilterTabDef`, `Option`, `ResolvedClassification`, generic `T`) or
  dropped `| null` (the ts-morph project runs `strict: false`). Re-check these if their props change.
- `componentSrcMap` pins `Th`/`GroupHeaderRow` to `ListTable.tsx` (file name ≠ export name).
- Overlays (`Modal`, `ConfirmDialog`, `UnsavedChangesDialog`, `DetailPanel`) use `cardMode: single`
  with a viewport. Their previews wrap each story in a sized `Frame` div: the card's `.ds-single`
  wrapper is transformed (so it's the containing block for `position: fixed`) but has no height of its
  own, and without the Frame the dialog centres in a 0-px box and gets cropped.
- Wide/stacked stories (`Th`, `GroupHeaderRow`, `FilterTabs`, `StockCountFields`, `FieldRow`,
  `ErrorBanner`) use `cardMode: column` after `[GRID_OVERFLOW]` flagged them.

## Known render warns

- None outstanding on the final build (29/29 clean, no thin/blank/identical).

## Re-sync risks

- **Tailwind vocabulary drift**: the design agent is told (conventions.md) that only classes in
  `styles.css` exist. If the app stops using a class the header names, the header goes stale — the
  validation script idea is in `.design-sync/.cache/check-conventions.mjs` (gitignored; re-create from
  the header's own class list if lost): grep every backticked class against `_ds_bundle.css`.
- `dtsPropsFor` bodies are hand-copied prop shapes; they silently drift if the six components' props
  change. Diff them against the source on each re-sync.
- Preview content is domain prose (SKUs, supplier names) invented for the cards — not tied to app
  data, so nothing upstream invalidates it, but it also won't track copy changes in the app.
- The `.d.ts` build inherits `frontend/tsconfig.json`; a `strict`/`noUnused*` tightening there can
  fail `tsc -p design-system/tsconfig.json` on component sources that the app build (`noEmit`) also
  checks — same errors, so it shouldn't surprise.
- No fonts are shipped (system stack) — if the app ever adopts a web font, add it via `cfg.extraFonts`.

## Candidates to add next

`Disclosure`, `SettingsCard`, `SettingsNav` (settings/), `PurchaseStatusPill`, `PlatformSyncBadge` — all
render standalone. Left out of the first sync because the user scoped it to `common/`.
