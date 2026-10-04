# Relay for Waypoint

**One plan, four faces.** Relay plans Waypoint's next-day deliveries under real limits and keeps everyone who
carries them out on the same page: the **dispatcher** who plans, the **loader** at the dock, the **driver** on the
road (with or without signal) and the **store manager** who orders and receives.

Built for the Rootcode Tech-Triathlon 2026 hackathon by TeamName.

<!-- Before submitting: replace the placeholders in this table and enable the CI badge below. -->
<!-- [![CI](https://github.com/<owner>/TeamName_Relay/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/TeamName_Relay/actions/workflows/ci.yml) -->

| | |
|---|---|
| **Live app** | _https://… (Cloud Run URL, added at submission)_ |
| **Demo video** | _YouTube link, added at submission_ |
| **Sign in** | any account below, password `relay2026` |
| **Run it yourself** | `docker compose up --build`, then http://localhost:8080 |

| Role | Email | Best on | Who they are |
|---|---|---|---|
| Dispatcher | `nirosha@waypoint.lk` | desktop (1440 px or wider) | Nirosha Perera, Peliyagoda planning office |
| Loader | `kasun@waypoint.lk` | tablet or phone | Kasun Bandara, Kandy Hub, Dock 1 |
| Driver | `sunil@waypoint.lk` | phone | Sunil Rathnayake, VEH057 reefer van |
| Store manager | `fathima@waypoint.lk` | desktop or phone | Fathima Rizwan, Nuwara Eliya Town (OUT105) |

Two more accounts exist for trying things in parallel: `ravi@waypoint.lk` (loader, Peliyagoda) and
`roshan@waypoint.lk` (driver, VEH042).

---

## The problem

Waypoint delivers to 120 outlets from two depots. Every afternoon at 4 PM the stores' orders close and somebody has
to fit tomorrow's demand into the vehicles that are actually available: reefers for chilled goods, vans for
outlets trucks cannot reach, one brand and one district per trip, a 270-minute pre-dawn window for Fresh, weekly
fuel quotas, and vehicles in the workshop. Demand usually exceeds cold capacity, so some orders must wait, and the
same stores keep getting skipped. Then the plan meets reality: a short shipment at the dock, a van that loses
signal in the hills, a damaged case at the store.

## What Relay does

| Face | What the person can do |
|---|---|
| **Dispatcher** (`/dispatch`) | Watch orders arrive until the cutoff. **Close orders and plan**: the CP-SAT engine plans both depots in seconds under every rule. See every trip on a zoomable canvas (depots, trips, stops, Window Bars, risk). Open the **deferral lever** to compare *Max throughput*, *Balanced* and *Fairness first*; every deferred order is explained as **unavoidable** or **a choice**, with its cost. Drag an order to another trip (the server checks it and refuses with a sentence if it breaks a rule). Decide shortfalls flagged at the dock. See vans with **No signal**, their estimated position, and queue changes for them. Read the 10-week cold-capacity outlook. Demo controls: virtual clock, reset, private sandboxes. Light and dark themes. |
| **Loader** (`/dock`) | The dock's queue of trucks. For each truck, the load list in **reverse stop order** (load first what is delivered last) with a load map. Tick lines, **flag** a line as missing, damaged or wrong (the release locks until the dispatcher decides), see plan changes as they happen ("take it off the truck"), then **release** with temperature, seal number, doors check and a swipe. Works offline on the shared tablet. |
| **Driver** (`/driver`) | The run, stop by stop: next stop with its window and ETA, swipe on arrival, a read-only delivery receipt with known shortfalls marked (the dock counts out, the store counts in), outcome (delivered, refused, store closed), proof (a photo and the store manager's delivery PIN, typed on the driver's phone), problem reports. **Everything works without signal**: records wait in the phone's outbox and sync with their original times. English, Sinhala and Tamil; light and dark. |
| **Store manager** (`/store`) | Today's deliveries with the Window Bar and expected arrival, the cutoff countdown, ordering (pre-filled from the standing order, chilled and ambient), tracking (Ordered → Planned → Loaded → On road → Delivered → Received, "estimated" while the van is dark), proof of delivery, **confirm receipt** with per-line issues that reach dispatch at once, and a Profile page with the manager's delivery PIN. |

Every change reaches the faces it affects within a couple of seconds (Postgres `LISTEN/NOTIFY` → Server-Sent
Events → refetch).

## Judge walkthrough (about 15 minutes)

Use two browser windows (or a desktop and a phone). Times on screen are the **demo clock**, which starts on
Tuesday 29 September at 15:20, forty minutes before Wednesday's ordering cutoff, and moves forward as people act.

> **Tip:** on the sign-in page, choose **Start a private sandbox**. You get a code like `RLY-7K2Q`; sign in to every
> role with it (**I have a demo workspace code**) and nobody else sees your changes. To start again, open the
> dispatcher's **Demo** menu and choose **Reset demo**.

1. **Store orders before the cutoff.** Sign in as **Fathima** (store). *Today* shows Wednesday's deliveries and
   the cutoff card ("40 min left"). Open **Order**, add a case of Yoghurt with **+**, then **Place order for
   Wednesday**. The confirmation shows the order references.
2. **Dispatcher plans the day.** In a second window sign in as **Nirosha** (dispatcher). The left feed shows the
   orders closing at 4:00 PM and the repeat skips from Tuesday. Choose **Close orders and plan**. In about 10
   seconds the engine plans both depots, explains every deferral, and the canvas fills with trips.
3. **Explore the plan.** Scroll to zoom, drag to pan, hover a trip or stop for its Window Bar and relay steps,
   search `VEH057`, and open the inspector. Drag a chilled order onto a dry truck: the server refuses it and says
   why.
4. **The deferral lever.** Open **Deferrals** in the left rail. Compare the three policies (orders served, chilled
   m³, repeat skips), read why each order waits (**unavoidable** or **choice**, and what serving it would cost), pick
   **Fairness first** and **Apply to the draft**. Then **Publish v2** in the top bar. Fathima's *Today* now shows
   the arrival window.
5. **Short at the dock.** Sign in as **Kasun** (loader), ideally on a phone or tablet. Tap your name in
   **Who's loading?**, open **VEH057**, tick a few lines (load order is the reverse of the stop order), then
   **Flag** a line as missing and **Flag and tell Nirosha**. The release locks.
6. **Dispatcher decides.** On the dispatcher, the feed shows the shortfall. Choose **Decide**, read the three
   options and their consequences, and take the recommended one. The loader, driver and store are told.
7. **Release.** Back on the loader: **Got it**, finish the lines (**All N lines are on**), then **Check and
   release**: seal number, doors closed, and **Swipe to release VEH057**.
8. **Driver on the road.** Sign in as **Sunil** (driver) on a phone. **Start run**, then at the first stop
   **Swipe when you arrive**. The delivery receipt shows what the dock loaded (the driver no longer counts). Then
   **Next: proof of delivery** and take a photo (or **No camera? Use a sample photo**). At Fathima's store, hand
   the phone to her: she types her delivery PIN into the hidden field and taps **Confirm PIN** (her demo PIN is
   **4826**, and she can see it in **Profile**). Then **Swipe to complete stop**.
9. **Dark corridor.** On the driver: **Me → Test offline mode** (or switch the phone to airplane mode). Deliver the
   next stop: it is saved on the phone and the banner says so. After about 45 seconds the dispatcher's trip shows
   **No signal** with an estimated position. On the dispatcher, search `VEH057`, open one of its remaining stops
   and choose **Move while dark…** with the suggested van: the move is **queued**, because the van cannot hear it.
10. **Back online.** Finish the remaining stops offline, then turn **Test offline mode** off. The **Sync** tab shows
    every record sent with the time it happened. The dispatcher sees **Conflict resolved: delivery kept**: the
    delivery made offline wins over the queued move, and the other van is told to skip it.
11. **Store confirms receipt.** On Fathima's *Today*, the delivery's proof carries the **Confirmed with your PIN**
    badge (open **Track**). **Confirm receipt**, mark one case **Damaged** and set the received quantity, then
    **Confirm and report 1 issue**. The dispatcher's feed shows the receipt issue at once.
12. **Outlook.** On the dispatcher, open **Outlook** for the 10-week cold-capacity forecast and the weeks short of
    reefer space.

The same journey runs automatically in a real browser: `e2e/tests/walkthrough.spec.ts`.

## Run it

### With Docker (the way judges run it)

Requirements: Docker Desktop (or OrbStack) with about 4 CPUs and 6 GB of memory.

```bash
git clone https://github.com/<owner>/TeamName_Relay.git && cd TeamName_Relay
docker compose up --build            # first build takes 5–8 minutes
open http://localhost:8080           # or visit it in any browser
```

The container applies the migrations, seeds the reference data, the accounts and the demo day, and serves the app.
`docker compose down -v` stops it and deletes the database.

### For development

Requirements: `uv`, Node 22+ with `pnpm` 10, Docker (for Postgres). A Mac from zero: [docs/SETUP_MAC.md](docs/SETUP_MAC.md).

```bash
make bootstrap      # uv sync + pnpm install + .env
make dev            # Postgres on :5433, API on :8000 (reload), web on :5173 (hot reload)
```

| Command | What it does |
|---|---|
| `make test` | Engine, predictor and API tests (Postgres starts if needed) |
| `make check-plan` | Plans the demo day under every policy and validates it against the allocation rules |
| `make e2e` | The four-role walkthrough in Playwright (first time: `make e2e-install`) |
| `make lint` / `make typecheck` / `make format` | Ruff, ESLint, Prettier, TypeScript |
| `make check` | Everything CI runs except the browser tests |
| `make reset-demo` | Reseeds the local shared demo |
| `make deploy` | Builds and deploys to Cloud Run ([docs/DEPLOY_GCP.md](docs/DEPLOY_GCP.md)) |

Configuration is by environment variable (see `.env.example`): `DATABASE_URL`, `SECRET_KEY`, `COOKIE_SECURE`,
`SEED_ON_START`, `SOLVER_TIME_LIMIT_S`, `HEARTBEAT_DARK_S`, `ALLOW_SANDBOXES`, `LOG_LEVEL`.

## How it is built

```
apps/api        FastAPI service: REST + Server-Sent Events + the built web app, one origin
apps/web        React 19 + TypeScript + Vite PWA: one sign-in, four role faces
packages/engine relay_engine: CP-SAT allocation, timetable, deferral explanations, validator, Task 2B CLI
packages/ml     relay_ml: service-time and lateness predictor, weekly demand forecast
e2e             Playwright tests (walkthrough, smoke, access)
infra/gcp       Cloud Run + Cloud SQL scripts
docs            architecture, engine, offline sync, data model, setup, deployment, plan
```

- **Architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): one container, Postgres as the only state, event
  log with `LISTEN/NOTIFY`, role-scoped read models, virtual clock, sandboxes, deployment.
- **Planning engine:** [docs/PLANNING_ENGINE.md](docs/PLANNING_ENGINE.md): the CP-SAT model, three policies,
  "unavoidable vs choice" explanations, manual moves, validation against the organisers' rules.
- **Offline and recovery:** [docs/OFFLINE_SYNC.md](docs/OFFLINE_SYNC.md): the outbox, idempotent sync, dark vans,
  "physical facts win".
- **Data model:** [docs/DATA_MODEL.md](docs/DATA_MODEL.md): tables, lifecycles, notices, seed data.
- **Design:** [docs/DESIGN_DEPARTURES.md](docs/DESIGN_DEPARTURES.md): where the build differs from our Figma and why.
- **AI use:** [docs/AI_DISCLOSURE.md](docs/AI_DISCLOSURE.md).

**Quality:** Ruff, ESLint, Prettier and strict TypeScript; 34 Python tests (engine rules and feasibility, the full
walkthrough over HTTP against real Postgres, idempotent offline sync with device times, the store-PIN handover,
security and scopes, ordering cutoff); an engine check that validates the demo day under every policy; Playwright tests across all four
roles; and a CI job that runs `docker compose up` from a clean checkout. All of it runs on every push
(`.github/workflows/ci.yml`).

## From the Datathon

The hackathon reuses our Datathon work rather than repeating it:

- **Stop predictions.** A LightGBM model trained on the delivery history predicts each planned stop's service
  minutes and its chance of arriving after the window (validation: service-time MAE 4.7 min against 7.3 for the
  standard allowance; lateness AUC 0.97). The dispatcher sees the risk on each stop and stores get an arrival band.
  Without the trained files the app uses a documented heuristic.
- **Capacity outlook.** The weekly demand forecast drives the dispatcher's 10-week cold-capacity chart.
- **Task 2B.** The same engine plans the S1 peak day: `make task2b`.

## Status and known limits

Everything in the walkthrough works end to end, locally and on Cloud Run. Known limits, stated plainly:

- Notices written by the server (feeds, alerts) are in English; the driver's interface is in English, Sinhala and
  Tamil.
- The dispatcher can flag but not reorder stops inside a trip; planned-late stops show as conflicts to resolve by
  moving orders.
- Only the driver and the loader work offline. The dispatcher and the store need a connection (and say so).
- Stock levels at depots and outlets are out of scope: Relay plans and tracks orders, not inventory.
- The fixed demo data is synthetic and fictional; it is derived from the competition's reference files.

## Data and licence notes

The competition's dataset terms do not allow publishing the data. This repository contains only the six small
reference files the seed needs (`data/reference/`); training and test files stay in the gitignored
`data/private/`, and so do the trained model files. Waypoint and every person in the demo are fictional.
