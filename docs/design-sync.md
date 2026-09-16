# Design sync (claude.ai/design)

StockSmith's shared UI primitives are published to the **StockSmith UI** design-system project on
claude.ai/design (`https://claude.ai/design/p/f0e6eb34-35ee-4131-9a0a-3f9aac2cb7eb`), so designs
produced there are built from the app's real compiled components and Tailwind sheet.

- **What's exported** is decided by the barrel at `frontend/design-system/index.ts`. Add an export
  there (plus a `docsMap` line in `.design-sync/config.json` for its group) to publish a component.
  Only components that render without router/query/API context belong in it.
- **`frontend/design-system/`** is a build-only sub-package: `npm run build` emits `.d.ts` files (the
  prop contracts the design agent codes against) and compiles the Tailwind sheet from `src/index.css`.
  It is not shipped with the app and has no runtime dependencies of its own.
- **`.design-sync/`** holds the sync inputs: `config.json`, per-component preview stories in
  `previews/`, the conventions header (`conventions.md`, prepended to the project README the design
  agent reads), group stubs, and `NOTES.md` with the gotchas a re-sync needs.
- **Re-syncing**: run `/design-sync` in Claude Code from the repo root. It rebuilds, diffs against the
  project's anchor, re-verifies only what changed, and uploads.
