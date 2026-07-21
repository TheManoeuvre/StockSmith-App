# StockSmith — Phase 0 & Phase 1 Implementation Plan

## Context

StockSmith is a new Windows desktop app for managing 3D-printing/resin product inventory: tracking raw materials, defining each product's Bill of Materials (BOM), and computing how many units of each product could be built from materials on hand ("max buildable") and what they're worth. This is the foundation for later phases (storefront quantity sync, order-driven stock decrements, print-queue management, shipping automation), so the data model is designed now with those in mind even though this plan only builds Phase 0 (setup) and Phase 1 (core inventory & BOM MVP) — no storefront/OAuth, order sync, print queue, or shipping logic yet.

The project directory (`C:\Users\Benja\iCloudDrive\Code\StockSmith`) is currently empty — this is a greenfield build, not a modification of existing code.

**Decisions confirmed with the user before this plan was written:**
- **Client**: Tauri (Rust shell + web frontend), produces a Windows `.msi`/`.exe` installer.
- **Backend topology**: a real client/server split — FastAPI + Postgres running on one "home base" PC on the user's home network, reachable from other devices via **Tailscale**. This is *not* a bundled local-SQLite app; it's the same architecture as a hosted backend, just self-hosted rather than cloud-hosted, so it's an easy swap to cloud hosting later if needed.
- **Variants**: `product_variants` (with BOM overrides) is included in v1, not deferred.
- **Costing**: weighted-average cost per material, recalculated on each purchase — no FIFO/lot tracking.

Note: because the backend and the asset file storage (OneDrive-synced folder, see below) can live on the same "home base" PC but are conceptually separate concerns (DB consistency vs. file sync), keep `ASSET_ROOT` and `DATABASE_URL` as independently configurable paths.

---

## Recommended Stack

- **Backend**: FastAPI, SQLAlchemy 2.0 (async, `asyncpg`), Alembic migrations, Pydantic v2 schemas/settings, `uv` for dependency management, `python-multipart`/raw-body streaming for uploads, `passlib` for the shared-password hash.
- **Frontend**: Tauri v2 + React + TypeScript + Vite, **TanStack Router** + **TanStack Query** (fits the "thin client over HTTP API" shape well), Tailwind + shadcn/ui for forms/tables, React Hook Form + zod.
- **Postgres**: run via Docker on the home-base PC (easy version pin/backup); FastAPI run as a native Windows process (simpler than containerizing alongside Tailscale networking).

---

## Repo Structure

```
StockSmith/
├── backend/                 # FastAPI service ("home base" API)
│   ├── alembic/              # migrations
│   └── app/
│       ├── main.py, config.py, db.py, deps.py, security.py
│       ├── models/           # SQLAlchemy models (material, product, variant, asset, listing)
│       ├── schemas/          # Pydantic request/response models
│       ├── routers/          # materials, purchases, products, boms, variants, assets, dashboard
│       ├── services/         # costing.py, buildability.py, file_storage.py
│       └── tests/
├── frontend/                 # Tauri app
│   ├── src-tauri/             # thin Rust shell, capabilities config
│   └── src/                   # React app: api/, routes/, components/, stores/, lib/tauri.ts
├── shared/openapi.json        # exported from FastAPI, used to generate frontend types
└── scripts/                   # gen-types, dev-up helpers
```

`.gitignore` the usual (venv, `node_modules`, `.env`, Rust `target/`). Note: keep the git repo itself out of active iCloud "optimize storage" sync friction — only `StockSmith-data/` (the asset folder) needs to be on a synced drive; the code repo doesn't.

---

## Database Schema (Phase 1)

Core tables — full DDL detail (types, constraints, indexes) to be written into the first Alembic migration:

- **materials**: id, name (unique), category enum (filament/resin/pigment/hardware/packaging/other), unit enum (g/ml/each), `current_qty`, `reorder_threshold`, **`avg_unit_cost`** (weighted-average cost per unit), `is_active`, timestamps.
- **material_purchases**: id, material_id FK, purchase_date, qty, total_cost, supplier, notes. Recording one recalculates the parent material's `current_qty` and `avg_unit_cost` (see Costing below).
- **products**: id, name, sku (unique), description, is_active, timestamps.
- **product_materials** (base BOM): product_id FK, material_id FK, qty_required — unique per (product, material).
- **product_variants**: id, product_id FK, variant_name, sku_suffix, is_active.
- **product_variant_materials** (BOM overrides): variant_id FK, material_id FK, qty_required. **Separate override table, not nullable columns on a merged view** — absence of a row means "inherit base BOM qty"; a `0` row means "this variant genuinely needs none of this material." This distinction matters and a merged/nullable design would conflate the two. Effective BOM for a variant = base `product_materials` rows LEFT JOIN this table on `(variant_id, material_id)`, preferring the override qty when present, UNION any variant-only materials not in the base BOM at all.
- **product_assets**: id, product_id FK, variant_id FK (nullable — null means "applies to base product"), asset_type enum (main_image/listing_image/step/threemf/gcode), file_path (relative path under `ASSET_ROOT`), original_filename, display_order.
- **listings**: included now per the original data model (product_id, variant_id, platform enum, external_listing_id, ceiling_qty, last_synced_qty/at) — schema only, zero sync logic in Phase 1, just avoids a disruptive migration when Phase 2 starts.

Add `updated_at` triggers on `materials`/`products`. Use `SERIAL` PKs (simplicity over UUIDs — single-tenant, no distributed-ID need).

---

## Computed Values

- **Weighted-average costing** (`services/costing.py`, unit-test this in isolation — it's the one piece of arithmetic that must be correct): on each purchase, in one transaction,
  ```
  new_total_value = current_qty * avg_unit_cost + purchase.total_cost
  new_qty         = current_qty + purchase.qty
  avg_unit_cost   = new_total_value / new_qty
  current_qty     = new_qty
  ```
  Also add a small **manual adjustment** endpoint (`POST /materials/{id}/adjustments`, signed qty delta + reason) so `current_qty` can move down (breakage, manual builds, physical recount) without it only ever increasing via purchases. Adjustments touch `current_qty` only, never `avg_unit_cost`.

- **max_buildable** (`services/buildability.py`): `MIN(FLOOR(material.current_qty / qty_required))` across a product's (or variant's resolved) BOM lines. Products/variants with zero BOM lines return `None` ("no BOM defined"), not 0 or infinity. Implement as set-based SQL for list/dashboard views (avoid N+1) and a simple parameterized query for single-item detail views.
- **inventory_value / cost_per_unit**: `inventory_value` = `SUM(current_qty * avg_unit_cost)` over active materials (trivial view). `cost_per_unit` per product/variant = `SUM(qty_required * avg_unit_cost)` over its (resolved) BOM.

---

## Backend API Surface (`/api/v1`, shared-password auth on all but `/healthz`)

- **Materials**: `GET/POST /materials`, `GET/PATCH/DELETE /materials/{id}` (qty/cost only ever change via purchases/adjustments, never direct PATCH).
- **Purchases**: `GET/POST /materials/{id}/purchases`, `POST /materials/{id}/adjustments`.
- **Products**: `GET/POST /products` (list includes computed `max_buildable`/`cost_per_unit`), `GET/PATCH/DELETE /products/{id}`.
- **BOM**: `GET /products/{id}/bom`, `PUT /products/{id}/bom` (bulk replace — matches a small editable-table UI better than per-row CRUD).
- **Variants**: `GET/POST /products/{id}/variants`, `GET/PATCH/DELETE /variants/{id}` (detail includes resolved effective BOM), `PUT /variants/{id}/bom-overrides` (bulk replace).
- **Assets**: `GET/POST /products/{id}/assets` (multipart/raw-body upload — see below), `PATCH/DELETE /assets/{id}`, `GET /assets/{id}/download` (auth-gated file serving — the client never touches the home-base filesystem directly).
- **Dashboard**: `GET /dashboard/summary` (low-stock materials, total inventory value, active product count), `GET /dashboard/max-buildable` (full per-product/variant list).
- `GET /healthz` unauthenticated, used by the Settings screen's "test connection."

---

## Asset Storage (network upload — the novel part)

Folder convention under a configured `ASSET_ROOT` (an env var on the home-base PC, e.g. pointing at an OneDrive-synced `StockSmith-data/` folder — deliberately independent of `DATABASE_URL`):

```
StockSmith-data/products/0007-oak-leaf-keyring/{images,cad,gcode}/...
```

- `services/file_storage.py`: resolves `products/{id:04d}-{slug(name)}/` (folder name fixed at product creation time — **renaming a product later does not rename its folder**, to avoid touching files mid-OneDrive-sync; flag this as a deliberate simplification, not a bug), creates `images/`/`cad/`/`gcode/` subfolders lazily by `asset_type`, handles filename collisions.
- The Tauri client never gets direct filesystem access to the home-base PC — all reads/writes go through the API (`POST .../assets` to upload, `GET /assets/{id}/download` to fetch bytes).
- Because Tauri's upload plugin support for true `multipart/form-data` is unclear from current docs, **spike this early** (Phase 0, see build order): try raw-body upload first (file bytes as the request body, metadata in headers), fall back to `plugin-http` fetch + manually-built `FormData` if raw-body proves awkward.
- Images need to be previewed in the UI despite requiring an auth header — plan a `useAssetUrl(assetId)` hook that fetches via authenticated JS and creates a blob URL, rather than a naive `<img src="http://homebase:8000/...">`.

---

## Frontend Structure (Phase 1 screens)

1. **Dashboard** — inventory value, low-stock materials, buildability list.
2. **Materials list/detail** — CRUD, purchase history table, "record purchase" form (recalculates avg cost live).
3. **Products list/detail** — CRUD, with a **BOM editor** (editable table, bulk save), **Variant editor** (per-variant override rows, greyed-out base qty for comparison), **Asset uploader** (per asset-type sections, drag-drop via Tauri dialog + upload plugin, thumbnails via the blob-URL hook), and a computed panel (max_buildable/cost_per_unit for base + each variant).
4. **Settings** — backend base URL (Tailscale MagicDNS name recommended over raw IP), shared password, "test connection" button; persisted via `@tauri-apps/plugin-store`.

Keep all Tauri plugin calls (dialog/upload/http/store) behind `frontend/src/lib/tauri.ts` so most UI code can run/iterate in a plain browser against the backend without needing a full Tauri build every time.

---

## Home-Base PC Setup

1. Postgres via Docker (`docker-compose.yml`, pinned version, volume for persistence).
2. Tailscale installed, enrolled in the user's tailnet; prefer the MagicDNS name (`homebase.tailnet-name.ts.net`) over a raw IP in app config since it's stable across IP changes.
3. `tailscale serve` to terminate TLS in front of the FastAPI port — cheap insurance against Tauri webview mixed-content/CSP restrictions, even though Tailscale's tunnel is already encrypted end-to-end.
4. `uvicorn app.main:app --host 0.0.0.0 --port 8000`, `alembic upgrade head` before first run.
5. `ASSET_ROOT` and `DATABASE_URL` set via `.env`.

## Auth

Single shared password, no user table/sessions/JWT — `passlib`-hashed, checked via a FastAPI dependency against a custom header on every request. Tailscale's network-level access control is the primary security boundary; the app password is defense-in-depth on top. Stored client-side in the Tauri plugin-store (plaintext JSON in the OS app-data dir) — acceptable trade-off for a single-user tailnet-only app; flag to the user as a simplification, revisit with an OS-keychain plugin later if desired.

---

## Build Order

**Phase 0**
1. Git init, `.gitignore`.
2. Backend skeleton: FastAPI + `/healthz` + Settings + SQLAlchemy engine → confirm local Postgres connectivity.
3. Alembic initial migration (full schema above) → `alembic upgrade head`, verify tables.
4. Tauri app scaffold (React+TS+Vite template) → confirm it can hit `localhost:8000/healthz`.
5. **Spike**: Tailscale from a second device → confirm reachability of `/healthz` over the tailnet. De-risks the architecture early.
6. **Spike**: smallest possible file-upload round trip (dialog → POST → lands on disk) → resolves raw-body-vs-multipart question before real asset endpoints are built.
7. Shared-password auth wired end-to-end.

**Phase 1** (roughly in dependency order)
1. Materials CRUD (backend + frontend) — establishes the router/schema/service pattern reused throughout.
2. Purchases + weighted-average costing + unit tests for the costing formula.
3. Products CRUD.
4. BOM CRUD + editor UI.
5. `max_buildable`/`cost_per_unit` service, exposed on product endpoints.
6. Variants + override table + resolved-BOM query + variant buildability.
7. Dashboard endpoint + screen (first full "integration" milestone).
8. Assets: folder-resolution + upload + download, then the uploader UI (done last — most novel, least coupled to the rest).
9. End-to-end pass from a second device over Tailscale: add material → record purchase → add product → build BOM → add variant → check dashboard → upload an image.

---

## Verification

- Unit tests for `services/costing.py` (weighted-average formula) and `services/buildability.py` (max_buildable across normal/zero-BOM/variant-override cases) — run via `pytest` in `backend/`.
- Manual end-to-end walkthrough per Phase 1 step 9 above, run from a second device over Tailscale (not just localhost), to validate the actual client/server/network architecture works, not just the logic.
- After the Phase 0 upload spike, confirm a file picked on a non-home-base device lands correctly in `ASSET_ROOT` on the home-base PC and is retrievable via the download endpoint with a correct thumbnail render in the UI.
- Confirm `tauri.conf.json` capabilities correctly scope the `http` plugin to the configured Tailscale host (Tauri v2 capability scoping) — verify a build fails closed (rejects requests) to hosts outside the configured one.
