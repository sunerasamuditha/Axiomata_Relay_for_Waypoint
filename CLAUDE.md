# CLAUDE.md: working on Relay

Relay is Waypoint's delivery-planning product for the Tech-Triathlon 2026 hackathon: **one plan, four faces**.
The dispatcher plans tomorrow under real limits, the loader loads in reverse stop order, the driver records every
stop even with no signal, and the store manager orders and confirms what arrived. One FastAPI service serves the
API, the live stream and the React app on one origin; Postgres is the only datastore.

Read `docs/ARCHITECTURE.md` before changing anything that crosses a boundary. The deeper references are
`docs/PLANNING_ENGINE.md`, `docs/OFFLINE_SYNC.md` and `docs/DATA_MODEL.md`.

## Commands

| Task | Command |
|---|---|
| Install everything (once) | `make bootstrap` |
| API (:8000, reload) + web (:5173, HMR) | `make dev` |
| Python tests (engine, ML, API flows; starts Postgres if needed) | `make test` |
| Lint + format check (ruff, ESLint, Prettier) | `make lint` (fix with `make format`) |
| TypeScript types (web + e2e) | `make typecheck` |
| Plan the demo day under every policy and validate it | `make check-plan` |
| Four-role browser walkthrough (needs `make dev`) | `make e2e` (first time: `make e2e-install`) |
| Everything CI runs except the browser tests | `make check` |
| Reseed the local shared demo | `make reset-demo` |
| Whole stack as judges run it | `docker compose up --build` → http://localhost:8080 |

Accounts (password `relay2026`): `nirosha@waypoint.lk` dispatcher, `kasun@waypoint.lk` loader,
`sunil@waypoint.lk` driver, `fathima@waypoint.lk` store manager.

## Repo map

```
apps/api/relay_api/      FastAPI app
  main.py                app, request log middleware, SPA serving
  routers/               auth, common (health, reference, media), dispatch, field (dock, driver, store, /sync), demo, stream (SSE)
  domain/                planning.py (close, solve, persist, publish, lever, moves), fieldops.py (dock, driver, sync,
                         heartbeat, dark detection, pending moves), ordering.py (cutoff, baskets), views.py (read models
                         per face), events.py (event log, notices, NOTIFY), clock.py (virtual clock), eta.py, simulate.py
  models.py, db.py       SQLAlchemy 2 models; engine/session; advisory-lock helper
  seed/                  reference CSVs + accounts + the demo day; `python -m relay_api.seed [--reset main]`
  check_plan.py          `make check-plan`
apps/api/alembic/        migrations (additive only during judging)
apps/web/src/            React 19 + TS + Vite PWA
  App.tsx, main.tsx      routing by role; each role is a lazy chunk with its own CSS
  lib/                   api.ts (fetch + CSRF header), auth.tsx, live.ts (SSE → React Query), clock.ts,
                         offline/ (Dexie outbox, flush, sync loop), format.ts, types.ts, pwa.ts
  ui/                    ds.css (design tokens), kit.tsx (WindowBar etc.), Icon.tsx, icons.ts
  roles/dispatch/        DispatchPage.tsx wraps engine/ (the approved HTML prototype, vanilla JS, its own DOM)
  roles/dock/            DockApp.tsx (loader), roles/driver/ DriverApp.tsx + i18n.ts (en/si/ta), roles/store/ StoreApp.tsx
packages/engine/         relay_engine: CP-SAT allocation, timetable, explanations, validator, Task 2B CLI (no DB, no web)
packages/ml/             relay_ml: service-time + lateness predictor (LightGBM, heuristic fallback), training, forecast
e2e/                     Playwright: walkthrough.spec.ts (four roles), smoke.spec.ts
infra/gcp/               setup.sh, deploy.sh, reset-demo.sh, github-wif.sh (read docs/DEPLOY_GCP.md)
data/reference/          the six competition CSVs the seed needs (the only competition data in git)
data/private/            gitignored: training/test CSVs for `make train`, `make forecast`, `make task2b`
```

## Invariants (do not break these)

1. **The server is the source of truth.** The web app renders server read models (`domain/views.py`); SSE only says
   *what changed* (topics) and the client refetches over REST.
2. **Every write goes through the event log.** Domain functions call `record(db, ws, type, ..., topics=[...])`
   (`domain/events.py`). Topics decide who refetches: `dispatch`, `plan`, `depot:<Kandy|Peliyagoda>`,
   `vehicle:<id>`, `outlet:<id>`, `clock`. A new write that forgets its topics leaves another face stale.
3. **Every role endpoint checks scope on the server.** Use the `dispatcher`, `loader`, `driver`, `store`
   dependencies from `deps.py`, and check ownership (a driver only touches its vehicle's trips, a loader only its
   depot, a store only its outlet). Hiding a button is not access control.
4. **Writes need the `X-Relay-Client: web` header** (CSRF guard in `deps.py`); `lib/api.ts` adds it.
5. **Field writes go through the outbox.** Driver and dock actions call `record(type, payload, label)` in
   `lib/offline/outbox.ts`, never `post()` directly, so they work with no signal. Each event has a `client_event_id`;
   `POST /api/sync` is idempotent and keeps the device's time.
6. **Physical facts win.** A delivery recorded on the phone beats a dispatcher move queued while the van was dark.
   Queued moves apply only after the phone's own records are in (`pending_after`).
7. **Times:** each workspace has a virtual clock (`domain/clock.py`, starts paused Tue 29 Sep 15:20). Use
   `now_virtual(ws)`, never `datetime.now()`, for business time. Engine minutes are relative to service-date midnight.
8. **Workspaces isolate data.** Every row carries `workspace_id`; judges get private sandboxes. Never query without it.
9. **Human sentences.** Errors, refusals and notices are written for the person reading them ("VEH012 has no
   refrigeration"), not codes. Domain errors raise `PlanningError` / `FieldError` / `OrderingError` → HTTP 409.
10. **No secrets and no private data in git.** Only `data/reference/*.csv` is committed. Never read `data/private/`
    from code that ships; never commit `.env`, `infra/gcp/env.sh` or model weights.

## Conventions

- **Python:** 3.12, ruff (line length 140), type hints, sync route handlers (FastAPI runs them in a threadpool),
  SQLAlchemy 2 style. Keep `relay_engine` pure: no database, no FastAPI imports.
- **Web:** TypeScript strict, React Query for server state (keys `["dispatch", ...]`, `["dock", ...]`,
  `["driver", ...]`, `["store", ...]`), `useLive(keys)` for invalidation, CSS per role on top of `ui/ds.css` tokens.
  Prettier (print width 160) formats everything except the dispatcher engine port.
- **Dispatcher desk:** `roles/dispatch/engine/*.js` is the approved prototype, ported. Keep its DOM, classes and
  `data-act` actions; new behaviour goes in `pages.js`/`canvas.js` and calls the API through `CTX.api`, then
  `CTX.refetch()`. Do not rewrite it in React.
- **Driver strings** live in `roles/driver/i18n.ts`; add every new key in `en`, `si` and `ta`.
- **Migrations:** `uv run alembic -c apps/api/alembic.ini revision --autogenerate -m "..."`, review the file, keep it
  additive (no drops or renames while judges use the live service).
- **Commits:** conventional (`feat(driver): …`, `fix(engine): …`), one concern per PR.

## Definition of done for a change

1. `make check` passes (lint, types, pytest, engine check).
2. `make e2e` passes when a user flow changed; add or extend a step in `e2e/tests/walkthrough.spec.ts`.
3. A bug fix comes with a regression test (API test in `apps/api/tests/` or engine test in `packages/engine/tests/`).
4. UI changes were looked at at the target size: dispatcher 1440×900, loader 1180×820 and 390×844, driver 390×844
   in light and dark, store 1280×820 and 390×844.
5. Docs updated when behaviour changed (`README.md` walkthrough, the relevant `docs/*.md`).

## Gotchas

- Sign-in and sign-out do a full page load on purpose: each role loads only its own CSS and code.
- `make dev` serves the web app on :5173 with an `/api` proxy; :8000 also serves `apps/web/dist` if it was built,
  which can be stale. Use :5173 while developing.
- The dock and driver show pending outbox records on top of server data (an overlay). If a screen "forgets" an
  action, check the overlay in `DockApp.tsx` / `DriverApp.tsx` before the server.
- A van is marked dark after `HEARTBEAT_DARK_S` (45 s) without a heartbeat, by the ticker that runs while someone
  watches the workspace. Driver → Me → "Test offline mode" simulates a dead zone without touching the network.
- The CP-SAT solve is time-limited (`SOLVER_TIME_LIMIT_S`, default 5 s per depot) and can differ slightly between
  runs; tests assert feasibility and explanations, not exact numbers.
