/**
 * Dock face (loader): the shared tablet at a depot dock, or a loader's phone.
 *
 *   queue    tonight's trucks by departure, with progress and status
 *   load     the load sheet in reverse stop order (last stop goes in first), tick lines, flag a
 *            shortfall to the dispatcher, side panel with meters, decisions and a load map
 *   release  last checks (reefer temperature, seal, doors, photo) and swipe to release
 *   released what the release told the driver, the dispatcher and the stores
 *
 * Every write goes through the offline outbox (Dexie): the dock keeps working when the Wi-Fi at the
 * bay drops, and the records sync in order with the time they happened.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Route, Routes, useNavigate, useParams } from "react-router";
import { errorText, get, post } from "../../lib/api";
import { signOut, useMe } from "../../lib/auth";
import { parseNaive, setClock, useVirtualNow } from "../../lib/clock";
import { dayLabel, dur, f1, hm, kg, minutesBetween } from "../../lib/format";
import { useLive, useLiveStatus } from "../../lib/live";
import { onSynced, record, useConnectivity, useOutbox, usePending, useSyncLoop } from "../../lib/offline/outbox";
import type { Crew, DockQueue, DockTrip, DockTripRow, DockVisit, Issue, Line, Notice } from "../../lib/types";
import { Icon } from "../../ui/Icon";
import { Drawer, Logo, PhotoInput, Swipe, ToastProvider, useToast } from "../../ui/kit";
import "../../ui/ds.css";
import "./dock.css";

/* ------------------------------------------------------------------ small helpers */

const CREW_KEY = "relay.dock.crew";
const ACK_KEY = "relay.dock.acked";

function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}
function save(key: string, v: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* private mode */
  }
}

const DOCK_LABEL: Record<string, string> = { rear_dock: "Rear dock", street: "Kerbside", mall_bay: "Mall bay" };
const KIND_WORD: Record<string, string> = { short_at_dock: "missing", damaged_at_dock: "damaged", wrong_at_dock: "wrong item" };
const first = (n: string) => n.split(" ")[0];
const initials = (n: string) =>
  n
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2);
const ord = (n: number) => {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
};
const vName = (type: string, temp: string) => (temp === "reefer" ? (type === "van" ? "Reefer van" : "Reefer truck") : type === "van" ? "Van" : "Truck");
const isReleased = (status: string) => ["loaded", "out", "done"].includes(status);
const tripOf = (n: Notice) => (n.entity?.startsWith("trip:") ? Number(n.entity.slice(5)) : null);
const changeKind = (n: Notice) => (n.key ?? "").split(":")[0];
const isChange = (n: Notice) => ["take", "new", "chg"].includes(changeKind(n));

/* ------------------------------------------------------------------ outbox overlay */

type Overlay = {
  checks: Map<number, { loaded: boolean; crew: string }>;
  flags: Map<number, { kind: string; qty: number; at: string; offline: boolean }>;
  releases: Map<number, { at: string; seal: string; temp_c: number | null; crew: string }>;
};

/** Records made on this device that the server views may not reflect yet. */
function useOverlay(since: number): Overlay {
  const items = useOutbox();
  return useMemo(() => {
    const o: Overlay = { checks: new Map(), flags: new Map(), releases: new Map() };
    for (const it of [...items].reverse()) {
      const fresh = it.status === "pending" || (it.status === "synced" && (it.syncedAt ?? 0) > since);
      if (!fresh) continue;
      const p = it.payload as Record<string, never>;
      if (it.type === "line.check") o.checks.set(p.line_id, { loaded: !!p.loaded, crew: p.crew ?? "" });
      if (it.type === "line.flag") o.flags.set(p.line_id, { kind: p.kind, qty: p.qty, at: it.at, offline: it.offline });
      if (it.type === "trip.release") o.releases.set(p.trip_id, { at: it.at, seal: p.seal, temp_c: p.temp_c ?? null, crew: p.crew ?? "" });
    }
    return o;
  }, [items, since]);
}

type LState = "todo" | "on" | "flag" | "part";

function lineState(l: Line, ov: Overlay): LState {
  const f = ov.flags.get(l.id);
  if (f) return "flag";
  if (l.flag && l.flag.status === "open") return "flag";
  const c = ov.checks.get(l.id);
  if (c) return c.loaded ? "on" : "todo";
  if (l.load_state === "loaded") return l.flag && l.flag.decision && l.flag.decision !== "hold" ? "part" : "on";
  if (l.load_state === "short" || l.load_state === "damaged") return "flag";
  return "todo";
}

function progress(visits: DockVisit[], ov: Overlay) {
  let total = 0;
  let done = 0;
  let waiting = 0;
  for (const v of visits)
    for (const l of v.lines) {
      total++;
      const s = lineState(l, ov);
      if (s === "on" || s === "part") done++;
      if (s === "flag") waiting++;
    }
  return { total, done, waiting, pct: total ? Math.round((done / total) * 100) : 0 };
}

/* ------------------------------------------------------------------ app */

export default function DockApp() {
  useLive([["dock"]]);
  useSyncLoop(false);
  return (
    <ToastProvider>
      <Routes>
        <Route index element={<Board />} />
        <Route path="t/:id" element={<Board />} />
        <Route path="t/:id/release" element={<Board release />} />
      </Routes>
    </ToastProvider>
  );
}

function useQueue() {
  const live = useLiveStatus();
  const q = useQuery<DockQueue>({ queryKey: ["dock", "queue"], queryFn: () => get("/api/dock/queue"), refetchInterval: live ? false : 15_000 });
  useEffect(() => {
    if (q.data) setClock(q.data.clock);
  }, [q.data]);
  return q;
}

function Board({ release }: { release?: boolean }) {
  const { id } = useParams();
  const sel = id ? Number(id) : null;
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const me = useMe().data!;
  const queue = useQueue();
  const trip = useQuery<DockTrip>({ queryKey: ["dock", "trip", sel], queryFn: () => get(`/api/dock/trips/${sel}`), enabled: !!sel });
  const ov = useOverlay(Math.min(queue.dataUpdatedAt || 0, trip.dataUpdatedAt || Infinity));
  const [crew, setCrewState] = useState<Crew | null>(() => load<Crew | null>(CREW_KEY, null));
  const [modal, setModal] = useState<"crew" | "changes" | "menu" | null>(null);
  const [sheet, setSheet] = useState<{ line: Line; visit: DockVisit } | null>(null);
  const [acked, setAcked] = useState<number[]>(() => load<number[]>(ACK_KEY, []));
  const q = queue.data;

  const setCrew = (c: Crew) => {
    setCrewState(c);
    save(CREW_KEY, c);
    setModal(null);
  };
  const ackDecision = (issueId: number) => {
    const next = [...acked, issueId];
    setAcked(next);
    save(ACK_KEY, next);
  };

  // after a sync: refetch, and say so when the server refused something
  useEffect(
    () =>
      onSynced((r) => {
        qc.invalidateQueries({ queryKey: ["dock"] });
        r.results.filter((x) => x.status === "rejected").forEach((x) => toast(x.message ?? "Not accepted", "ember", "alert"));
      }),
    [qc, toast],
  );
  useEffect(() => {
    const h = () => qc.invalidateQueries();
    window.addEventListener("relay:reset", h);
    return () => window.removeEventListener("relay:reset", h);
  }, [qc]);

  // desktop: open the first truck still to load
  useEffect(() => {
    if (sel || !q || window.matchMedia("(max-width: 860px)").matches) return;
    const firstOpen = q.trips.find((t) => !isReleased(t.status) && !ov.releases.has(t.id)) ?? q.trips[0];
    if (firstOpen) nav(`/dock/t/${firstOpen.id}`, { replace: true });
  }, [sel, q, nav, ov.releases]);

  // first use of the shared tablet: who is loading?
  useEffect(() => {
    if (q && !crew && q.crew_roster.length && modal === null) setModal("crew");
  }, [q, crew, modal]);

  const changes = (q?.changes ?? []).filter(isChange);
  const unread = changes.filter((n) => !n.read);
  const crewName = crew?.short ?? me.short_name;

  return (
    <div className="dock-root">
      <div className="app">
        <TopBar q={q} crew={crew} onCrew={() => setModal("menu")} />
        <ConnBar />
        {unread.length ? (
          <div className="planbar">
            <Icon name="layers" />
            <div className="pbt">
              <b>Plan changed at {hm(unread[unread.length - 1].at)}</b>
              <span>{summary(unread)}</span>
            </div>
            <button onClick={() => setModal("changes")}>
              Review changes <Icon name="arrowR" />
            </button>
          </div>
        ) : null}
        {queue.isLoading ? (
          <div className="dempty">
            <div className="skeleton" style={{ width: "min(560px,90%)", height: 220 }} />
          </div>
        ) : queue.error ? (
          <div className="dempty">
            <div className="card">
              <Icon name="alert" />
              <h2>Can't load the dock queue</h2>
              <p>{errorText(queue.error)}</p>
            </div>
          </div>
        ) : q && !q.plan ? (
          <div className="dempty">
            <div className="card">
              <Icon name="layers" />
              <h2>{q.pending_plan ? `${q.dispatcher} is planning` : "No plan yet"}</h2>
              <p>
                {q.pending_plan
                  ? "Tonight's trucks appear here the moment she publishes the plan."
                  : `Orders close at 16:00. Tonight's trucks appear here when ${q.dispatcher} publishes the plan.`}
              </p>
            </div>
          </div>
        ) : q ? (
          <div className={`main ${sel ? "has-sel" : ""}`}>
            <QueuePane q={q} sel={sel} ov={ov} />
            {sel && trip.data ? (
              <Detail
                key={sel}
                d={trip.data}
                q={q}
                ov={ov}
                release={!!release}
                crew={crewName}
                acked={acked}
                onAck={ackDecision}
                onFlag={(line, visit) => setSheet({ line, visit })}
                takeoffs={changes.filter((n) => changeKind(n) === "take" && !n.read && tripOf(n) === sel)}
                onTakeOff={(n) => ackChange(n, crewName, qc, toast)}
              />
            ) : sel && trip.error ? (
              <section className="dpane">
                <div className="dempty">
                  <div className="card">
                    <Icon name="alert" />
                    <h2>This truck is not on the plan any more</h2>
                    <p>{errorText(trip.error)}</p>
                    <button className="btn" style={{ marginTop: 14 }} onClick={() => nav("/dock")}>
                      Back to the queue
                    </button>
                  </div>
                </div>
              </section>
            ) : sel ? (
              <section className="dpane">
                <div className="skeleton" style={{ margin: 24, height: 300 }} />
              </section>
            ) : (
              <section className="dpane" />
            )}
          </div>
        ) : null}
      </div>
      {modal === "crew" && q ? <CrewDialog q={q} crew={crew} onPick={setCrew} onClose={() => crew && setModal(null)} /> : null}
      {modal === "menu" ? (
        <>
          <div className="backdrop" style={{ background: "transparent" }} onClick={() => setModal(null)} />
          <div className="menu-pop" role="menu">
            <div className="who">
              <b>{me.name}</b>
              <small>{me.title}</small>
            </div>
            <button role="menuitem" onClick={() => setModal("crew")}>
              <Icon name="user" /> Switch loader {crew ? `(now ${crew.short})` : ""}
            </button>
            <button role="menuitem" onClick={() => setModal("changes")}>
              <Icon name="layers" /> Plan changes
            </button>
            <button role="menuitem" onClick={signOut}>
              <Icon name="logout" /> Sign out
            </button>
          </div>
        </>
      ) : null}
      {modal === "changes" && q ? (
        <ChangesDrawer
          q={q}
          changes={changes}
          onClose={() => setModal(null)}
          onOpen={(tid) => {
            setModal(null);
            nav(`/dock/t/${tid}`);
          }}
          onAck={(n) => ackChange(n, crewName, qc, toast)}
        />
      ) : null}
      {sheet && trip.data && q ? (
        <FlagSheet
          d={trip.data}
          line={sheet.line}
          visit={sheet.visit}
          dispatcher={q.dispatcher}
          onClose={() => setSheet(null)}
          onSend={async (kind, qty, note, photo) => {
            await record(
              "line.flag",
              { line_id: sheet.line.id, kind, qty, note, photo, crew: crewName },
              `Flagged ${qty} × ${sheet.line.name} ${kind} · ${trip.data!.trip.vehicle}`,
            );
            setSheet(null);
            toast(`Flagged. ${q.dispatcher} has it now.`, "ember", "flag");
          }}
        />
      ) : null}
    </div>
  );
}

function summary(unread: Notice[]) {
  const takes = unread.filter((n) => changeKind(n) === "take").length;
  const news = unread.filter((n) => changeKind(n) === "new").length;
  const other = unread.length - takes - news;
  const parts = [];
  if (takes) parts.push(`${takes} to take off`);
  if (news) parts.push(`${news} new to stage`);
  if (other) parts.push(`${other} other change${other > 1 ? "s" : ""}`);
  return parts.join(" · ");
}

async function ackChange(n: Notice, crew: string, qc: ReturnType<typeof useQueryClient>, toast: ReturnType<typeof useToast>) {
  try {
    await post(`/api/dock/changes/${n.id}/ack`, { crew });
    qc.invalidateQueries({ queryKey: ["dock"] });
    if (changeKind(n) === "take") toast("Confirmed. Dispatch knows it is off the truck.", "green", "check");
  } catch (e) {
    toast(errorText(e), "ember", "alert");
  }
}

/* ------------------------------------------------------------------ top bar */

function TopBar({ q, crew, onCrew }: { q?: DockQueue; crew: Crew | null; onCrew: () => void }) {
  const now = useVirtualNow(5000);
  const me = useMe().data!;
  return (
    <header className="top">
      <div className="brand">
        <Logo size={32} />
        Relay
      </div>
      <span className="vsep" />
      <div className="where">
        <Icon name="warehouse" />
        <div>
          <b>
            {q?.depot_name ?? me.depot} · Dock {q?.dock ?? me.dock ?? 1}
          </b>
          <small>Night shift · Bays 1–5</small>
        </div>
      </div>
      <div className="sp" />
      <div className="clock">
        <Icon name="clock" />
        <b className="num">{hm(now)}</b>
        <small>{dayLabel(now)}</small>
      </div>
      <Conn />
      <button className={`crewbtn ${crew ? "" : "none"}`} onClick={onCrew} aria-label="Loader and account">
        {crew ? (
          <span className="av" style={{ background: crew.color }}>
            {crew.initials}
          </span>
        ) : (
          <Icon name="user" />
        )}
        <span>{crew ? crew.short : "Who's loading?"}</span>
        <Icon name="chevD" />
      </button>
    </header>
  );
}

function Conn() {
  const { online } = useConnectivity();
  const pending = usePending();
  const live = useLiveStatus();
  if (!online)
    return (
      <span className="connpill off">
        <Icon name="wifiOff" />
        Offline{pending ? ` · ${pending} saved` : ""}
      </span>
    );
  if (pending)
    return (
      <span className="connpill wait">
        <Icon name="sync" />
        Syncing {pending}
      </span>
    );
  return (
    <span className={`connpill ${live ? "" : "wait"}`}>
      <Icon name={live ? "check" : "sync"} />
      {live ? "Live" : "Reconnecting"}
    </span>
  );
}

function ConnBar() {
  const { online } = useConnectivity();
  const pending = usePending();
  if (online) return null;
  return (
    <div className="offbar" role="status">
      <Icon name="wifiOff" />
      No connection at the dock. Keep loading: every tick is saved on this tablet and syncs by itself.
      <b>{pending} waiting</b>
    </div>
  );
}

/* ------------------------------------------------------------------ queue */

function tstat(t: DockTripRow, ov: Overlay, takeoff: boolean, isNew: boolean): { cls: string; icon?: string; dot?: boolean; txt: string } {
  const rel = ov.releases.get(t.id);
  if (rel) return { cls: "green", icon: "check", txt: `Released ${hm(rel.at)}` };
  if (isReleased(t.status)) return { cls: "green", icon: "check", txt: t.status === "loaded" ? "Released" : t.status === "out" ? "On the road" : "Back" };
  if (takeoff) return { cls: "ember", icon: "layers", txt: "Plan changed" };
  if (t.flags_open) return { cls: "ember", icon: "flag", txt: "Short · waiting" };
  if (t.lines_total && t.lines_done === t.lines_total) return { cls: "green", icon: "check", txt: "Ready to release" };
  if (isNew && t.lines_done === 0) return { cls: "blue", icon: "plus", txt: `New · stage by ${hm(parseNaive(t.depart) - 20 * 60000)}` };
  if (t.lines_done > 0) return { cls: "blue", dot: true, txt: `Loading${t.crew ? ` · ${t.crew}` : ""}` };
  return { cls: "", icon: "clock", txt: `Load from ${hm(parseNaive(t.depart) - 75 * 60000)}` };
}

function QueuePane({ q, sel, ov }: { q: DockQueue; sel: number | null; ov: Overlay }) {
  const now = useVirtualNow(15_000);
  const nav = useNavigate();
  const changes = q.changes.filter((n) => !n.read && isChange(n));
  const live = q.trips.filter((t) => !isReleased(t.status) && !ov.releases.has(t.id));
  const gone = q.trips.filter((t) => isReleased(t.status) || ov.releases.has(t.id));
  const row = (t: DockTripRow) => {
    const takeoff = changes.some((n) => changeKind(n) === "take" && tripOf(n) === t.id);
    const isNew = changes.some((n) => changeKind(n) === "new" && tripOf(n) === t.id);
    const st = tstat(t, ov, takeoff, isNew);
    const released = isReleased(t.status) || ov.releases.has(t.id);
    const left = minutesBetween(now, t.depart);
    const pct = released ? 100 : t.lines_total ? Math.round((t.lines_done / t.lines_total) * 100) : 0;
    const hot = !released && left <= 15 && t.lines_done < t.lines_total;
    const warn = t.flags_open > 0 || takeoff;
    return (
      <button key={t.id} className={`qrow ${sel === t.id ? "sel" : ""} ${released ? "gone" : ""}`} onClick={() => nav(`/dock/t/${t.id}`)}>
        <div className="tm">
          <b>{hm(t.depart)}</b>
          <small className={hot ? "hot" : ""}>{released ? "" : left > 0 ? `in ${dur(left)}` : "now"}</small>
          <small className="bay">Bay {t.bay}</small>
        </div>
        <div className="mid">
          <div className="v">
            {t.vehicle}
            {t.temp === "reefer" ? (
              <span className="snow">
                <Icon name="snow" />
              </span>
            ) : null}
            <span className="tag">
              T{t.trip_no} · {t.type === "van" ? "Van" : "Truck"}
            </span>
            <span className={`pct ${pct >= 100 ? "ok" : warn ? "warn" : ""}`}>{pct >= 100 ? <Icon name="check" /> : `${pct}%`}</span>
          </div>
          <div className="m">
            {t.brand} · {t.district} · {t.n_stops === 1 ? "1 stop" : `${t.n_stops} stops`}
            {t.controlled === "human" ? "" : ""}
          </div>
          <div className="chips">
            <span className={`chip ${st.cls}`}>
              {st.dot ? <i className="dot" style={{ background: "var(--blue)" }} /> : <Icon name={st.icon ?? "clock"} />}
              {st.txt}
            </span>
          </div>
          <div className={`qbar ${pct >= 100 ? "ok" : warn ? "warn" : ""}`}>
            <i style={{ width: `${pct}%` }} />
          </div>
        </div>
      </button>
    );
  };
  return (
    <aside className="qpane">
      <div className="qhead">
        <div className="spread">
          <h2>Tonight's trucks</h2>
          {q.plan ? (
            <span className="chip outline mono" style={{ fontSize: 11.5 }}>
              Plan v{q.plan.version} · {hm(q.plan.published_at)}
            </span>
          ) : null}
        </div>
        <p className="muted">{live.length} to go · by departure time</p>
      </div>
      <div className="qlist">
        {live.map(row)}
        {gone.length ? (
          <>
            <div className="qgroup">Released · {gone.length}</div>
            {gone.map(row)}
          </>
        ) : null}
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------ detail */

type DetailProps = {
  d: DockTrip;
  q: DockQueue;
  ov: Overlay;
  release: boolean;
  crew: string;
  acked: number[];
  onAck: (issueId: number) => void;
  onFlag: (l: Line, v: DockVisit) => void;
  takeoffs: Notice[];
  onTakeOff: (n: Notice) => void;
};

function Detail(p: DetailProps) {
  const { d, ov } = p;
  const rel = ov.releases.get(d.trip.id);
  if (isReleased(d.trip.status) || rel) return <ReleasedView {...p} />;
  if (p.release) return <ReleaseView {...p} />;
  return <LoadView {...p} />;
}

function DHead({ d }: { d: DockTrip }) {
  const now = useVirtualNow(15_000);
  const nav = useNavigate();
  const t = d.trip;
  const left = minutesBetween(now, t.depart);
  const hot = left <= 15 && d.progress.done < d.progress.total;
  return (
    <div className="dhead">
      <div style={{ minWidth: 0 }} className="row">
        <button className="backbtn backlink" onClick={() => nav("/dock")} aria-label="Back to the queue">
          <Icon name="chevL" />
        </button>
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">
            Bay {t.bay} · Trip {t.trip_no} · {t.brand} · {t.district}
          </div>
          <div className="vt">
            <h1>{t.vehicle}</h1>
            {t.temp === "reefer" ? (
              <span className="snow">
                <Icon name="snow" />
              </span>
            ) : null}
            <span className="chip">
              {vName(t.type, t.temp)} · {f1(t.cap_m3)} m³ · {kg(t.cap_kg)} kg
            </span>
          </div>
        </div>
      </div>
      <div className="dep">
        <div className="depL">
          <small>Departs</small>
          <b>{hm(t.depart)}</b>
          <span className={hot ? "hot" : ""}>{left > 0 ? `in ${dur(left)}` : "now"}</span>
        </div>
        <div className="drvc">
          <span className="av" style={{ background: "var(--ember)" }}>
            {initials(t.driver)}
          </span>
          <div>
            <small>Driver</small>
            <b>{t.driver}</b>
          </div>
        </div>
      </div>
    </div>
  );
}

function LoadView({ d, q, ov, crew, acked, onAck, onFlag, takeoffs, onTakeOff }: DetailProps) {
  const now = useVirtualNow(15_000);
  const nav = useNavigate();
  const toast = useToast();
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const t = d.trip;
  const visits = d.load_order; // already last stop first
  const prog = progress(visits, ov);
  const nextLine = useMemo(() => {
    for (const v of visits) for (const l of v.lines) if (lineState(l, ov) === "todo") return l.id;
    return null;
  }, [visits, ov]);
  const left = minutesBetween(now, t.depart);
  const ready = prog.total > 0 && prog.done === prog.total && !prog.waiting && takeoffs.length === 0;
  const tick = useCallback(
    (l: Line) => {
      const st = lineState(l, ov);
      if (st === "flag" || st === "part") return;
      const loaded = st !== "on";
      void record("line.check", { line_id: l.id, loaded, crew }, `${loaded ? "Loaded" : "Unticked"} ${l.name} · ${t.vehicle}`);
    },
    [ov, crew, t.vehicle],
  );
  const loadAll = (v: DockVisit) => v.lines.filter((l) => lineState(l, ov) === "todo").forEach(tick);
  const issues = d.issues.filter((i) => i.kind.endsWith("_at_dock"));
  const lineById = new Map(visits.flatMap((v) => v.lines.map((l) => [l.id, { l, v }] as const)));

  let foot: ReactNode;
  if (takeoffs.length)
    foot = (
      <div className="ft warn">
        <b>
          <Icon name="layers" />
          Take off the moved stop first
        </b>
        <small>{prog.total - prog.done} lines to go</small>
      </div>
    );
  else if (prog.waiting)
    foot = (
      <div className="ft warn">
        <b>
          <i className="dot pulse" />
          Waiting for {q.dispatcher}
        </b>
        <small>Release unlocks when she decides. Keep loading meanwhile.</small>
      </div>
    );
  else if (ready)
    foot = (
      <div className="ft ok">
        <b>
          <Icon name="check" />
          Every line is on the truck or decided.
        </b>
        <small>Run the last checks before {first(t.driver)} leaves.</small>
      </div>
    );
  else {
    const rem = prog.total - prog.done;
    const tight = rem * 1.5 > left - 5;
    foot = (
      <div className={`ft ${tight ? "warn" : ""}`}>
        <b>{rem} lines to go</b>
        <small>{tight ? `Tight: ${dur(Math.max(0, left))} to departure` : `On pace for ${hm(t.depart)}`}</small>
      </div>
    );
  }

  return (
    <section className="dpane">
      <DHead d={d} />
      <div className="dbody">
        <div className="dlist">
          <div className="slist">
            <div className="shead">
              <h3>Load in this order</h3>
              <span className="muted">Tap a line once it is on the truck.</span>
            </div>
            {takeoffs.map((n) => (
              <div key={n.id} className="sg take">
                <div className="gh">
                  <span className="sq">
                    <Icon name="minus" />
                  </span>
                  <div className="gt">
                    <b>
                      {n.title.replace(/^(Unseal and take|Take) off /, "").replace(` from ${t.vehicle}`, "")}
                      <span className="chip ember">
                        <Icon name="arrowR" />
                        Moved off this truck
                      </span>
                    </b>
                    <small>{n.body}</small>
                  </div>
                </div>
                <p className="taketip">
                  <Icon name="info" />
                  <span>Loaded early, so it sits towards the cab. Pull the stops in front of it forward to reach it.</span>
                </p>
                <button className="btn ember block lg" onClick={() => onTakeOff(n)}>
                  <Icon name="check" />
                  It's off the truck
                </button>
              </div>
            ))}
            {visits.map((v, gi) => {
              const states = v.lines.map((l) => lineState(l, ov));
              const done = states.filter((s) => s === "on" || s === "part").length;
              const flagged = states.filter((s) => s === "flag").length;
              const full = done === v.lines.length && !flagged;
              const decidedPending = v.lines.some((l) => l.flag && l.flag.status !== "open" && !acked.includes(l.flag.issue_id));
              const open = !full || decidedPending || expanded[v.stop_id];
              const tag = <span className={`chip ${gi === 0 ? "dark" : "outline"} lo`}>Load {ord(gi + 1)}</span>;
              const cur = v.lines.some((l) => l.id === nextLine);
              if (!open) {
                const who = [...new Set(v.lines.map((l) => ov.checks.get(l.id)?.crew || l.loaded_by).filter(Boolean))].join(", ");
                return (
                  <button key={v.stop_id} className="sg done" onClick={() => setExpanded({ ...expanded, [v.stop_id]: true })} aria-expanded="false">
                    <div className="gh">
                      <span className="sq">
                        <Icon name="check" />
                      </span>
                      <div className="gt">
                        <b>{v.name}</b>
                        <small>
                          Stop {v.seq} · {v.lines.length} lines · {who || "—"}
                        </small>
                      </div>
                      {tag}
                      <Icon name="chevD" />
                    </div>
                  </button>
                );
              }
              return (
                <div key={v.stop_id} className={`sg ${cur ? "cur" : ""}`}>
                  <div
                    className="gh"
                    {...(full
                      ? {
                          role: "button",
                          tabIndex: 0,
                          "aria-expanded": true,
                          style: { cursor: "pointer" },
                          onClick: () => setExpanded({ ...expanded, [v.stop_id]: false }),
                        }
                      : {})}
                  >
                    <span className="sq">{v.seq}</span>
                    <div className="gt">
                      <b>
                        {v.name}
                        {v.temp === "chilled" ? (
                          <span className="snow">
                            <Icon name="snow" />
                          </span>
                        ) : null}
                      </b>
                      <small>
                        {v.temp === "chilled" ? "Chilled" : "Ambient"} · {v.units} cs · {f1(v.m3)} m³ · {v.window[0]}–{v.window[1]} ·{" "}
                        {DOCK_LABEL[v.dock_type] ?? v.dock_type}
                      </small>
                    </div>
                    {tag}
                    {full ? <Icon name="chevU" /> : null}
                  </div>
                  <div className="lines">
                    {v.lines.map((l) => (
                      <LineRow key={l.id} l={l} st={lineState(l, ov)} ov={ov} next={l.id === nextLine} onTick={() => tick(l)} onFlag={() => onFlag(l, v)} />
                    ))}
                  </div>
                  {!full && v.lines.length > 2 && done === 0 && !flagged ? (
                    <button className="chip outline" style={{ marginTop: 10, height: 34 }} onClick={() => loadAll(v)}>
                      <Icon name="check" /> All {v.lines.length} lines are on
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
        <aside className="dside">
          {issues.map((i) => (
            <Strip
              key={i.id}
              i={i}
              d={d}
              q={q}
              line={lineById.get(i.line ?? -1)}
              acked={acked.includes(i.id)}
              onAck={() => onAck(i.id)}
              crew={crew}
              toast={toast}
            />
          ))}
          {[...ov.flags.entries()]
            .filter(([lid]) => lineById.has(lid) && !issues.some((i) => i.line === lid && i.status === "open"))
            .map(([lid, f]) => (
              <div key={`p${lid}`} className="strip ember">
                <div className="sth">
                  <span className="si">
                    <Icon name="flag" />
                  </span>
                  <b>{f.offline ? "Saved on this tablet" : `Telling ${q.dispatcher}…`}</b>
                </div>
                <p>
                  {f.qty} × {lineById.get(lid)!.l.name} {f.kind === "wrong" ? "wrong item" : f.kind}.{" "}
                  {f.offline ? "It reaches her as soon as the dock is back online." : "Sending now."}
                </p>
              </div>
            ))}
          <Meters d={d} prog={prog} />
          <LoadMap d={d} ov={ov} nextLine={nextLine} />
        </aside>
      </div>
      <div className="dfoot">
        {foot}
        <button className={`btn ${ready ? "primary" : ""} lg`} disabled={!ready} onClick={() => nav(`/dock/t/${t.id}/release`)}>
          Check and release <Icon name="arrowR" />
        </button>
      </div>
    </section>
  );
}

function LineRow({ l, st, ov, next, onTick, onFlag }: { l: Line; st: LState; ov: Overlay; next: boolean; onTick: () => void; onFlag: () => void }) {
  const pend = ov.checks.has(l.id) || ov.flags.has(l.id);
  const f = l.flag;
  const ovf = ov.flags.get(l.id);
  const qty = f?.qty ?? ovf?.qty ?? 0;
  const kindWord = f ? (KIND_WORD[f.kind] ?? f.kind) : ovf ? (ovf.kind === "wrong" ? "wrong item" : ovf.kind) : "";
  const who = ov.checks.get(l.id)?.crew || l.loaded_by;
  const sub =
    st === "flag"
      ? `${qty} ${kindWord} · waiting for a decision`
      : st === "part"
        ? `${l.qty - qty} of ${l.qty} go · ${f?.decided_by ? `approved by ${f.decided_by}` : "approved"}`
        : st === "on"
          ? `On the truck${who ? ` · ${who}` : ""}`
          : l.uom === "case"
            ? "To load"
            : `To load · ${l.uom}`;
  const tickable = st === "todo" || st === "on";
  return (
    <div
      className={`lrow ${st} ${next ? "next" : ""} ${pend ? "pend" : ""}`}
      {...(tickable
        ? {
            role: "button",
            tabIndex: 0,
            "aria-pressed": st === "on",
            onClick: (e: React.MouseEvent) => {
              if ((e.target as HTMLElement).closest(".fl")) return;
              onTick();
            },
            onKeyDown: (e: React.KeyboardEvent) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onTick();
              }
            },
          }
        : {})}
    >
      <span className="tick">{st === "on" ? <Icon name="check" /> : st === "flag" ? <Icon name="flag" /> : st === "part" ? <Icon name="split" /> : null}</span>
      <div className="ln">
        <div className="nm">
          {l.name}
          {next ? <span className="chip blue xs">Next</span> : null}
        </div>
        <div className="sub">{sub}</div>
      </div>
      <span className="qt">
        {st === "part" ? (
          <>
            {l.qty - qty}
            <small>/{l.qty}</small>
          </>
        ) : (
          l.qty
        )}
        <small> cs</small>
      </span>
      {tickable ? (
        <button
          className="fl"
          onClick={(e) => {
            e.stopPropagation();
            onFlag();
          }}
          aria-label={`Issue: ${l.name}`}
        >
          <Icon name="flag" />
          <span>Issue</span>
        </button>
      ) : null}
    </div>
  );
}

function Strip({
  i,
  d,
  q,
  line,
  acked,
  onAck,
  crew,
  toast,
}: {
  i: Issue;
  d: DockTrip;
  q: DockQueue;
  line?: { l: Line; v: DockVisit };
  acked: boolean;
  onAck: () => void;
  crew: string;
  toast: ReturnType<typeof useToast>;
}) {
  const [busy, setBusy] = useState(false);
  const name = line?.l.name ?? "items";
  if (i.status === "open")
    return (
      <div className="strip ember">
        <div className="sth">
          <span className="si">
            <Icon name="flag" />
          </span>
          <b>Waiting for {q.dispatcher}</b>
        </div>
        <p>
          {i.qty} × {name} {KIND_WORD[i.kind] ?? ""}. Sent at {hm(i.raised_at)}. Keep loading the other lines.
        </p>
        <small>
          No answer by {hm(parseNaive(d.trip.depart) - 10 * 60000)}? {d.trip.vehicle} leaves with what is loaded and the rest follows on the next run.
        </small>
        <button
          className="btn ghost"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await post(`/api/dock/issues/${i.id}/remind`, { crew });
              toast(`${q.dispatcher} was reminded. It is at the top of her feed.`, "", "bell");
            } catch (e) {
              toast(errorText(e), "ember", "alert");
            } finally {
              setBusy(false);
            }
          }}
        >
          <Icon name="bell" />
          Remind {q.dispatcher}
        </button>
      </div>
    );
  if (acked) return null;
  const store = line ? `${line.v.manager ? first(line.v.manager) : line.v.name}` : "the store";
  return (
    <div className="strip blue">
      <div className="sth">
        <span className="si">
          <Icon name="check" />
        </span>
        <b>
          {i.decided_by ?? q.dispatcher} decided at {hm(i.decided_at)}
        </b>
      </div>
      <p>
        {i.decision_text}. {first(d.trip.driver)} and {store} have been told.
      </p>
      <button className="btn primary" onClick={onAck}>
        <Icon name="check" />
        Got it
      </button>
    </div>
  );
}

function Meters({ d, prog }: { d: DockTrip; prog: { total: number; done: number } }) {
  const t = d.trip;
  const m3 = d.progress.m3;
  const w = d.progress.kg;
  const row = (lab: string, val: string, pct: number, cls: string) => (
    <div className="mrow">
      <div className="spread">
        <span>{lab}</span>
        <b>{val}</b>
      </div>
      <div className={`capbar ${cls}`}>
        <i style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
      </div>
    </div>
  );
  return (
    <div className="scard">
      {row("Lines", `${prog.done} / ${prog.total}`, prog.total ? (prog.done / prog.total) * 100 : 0, prog.done === prog.total && prog.total ? "ok" : "")}
      {row("Volume", `${f1(m3)} / ${f1(t.cap_m3)} m³`, (m3 / t.cap_m3) * 100, m3 / t.cap_m3 > 0.95 ? "hi" : "")}
      {row("Weight", `${kg(w)} / ${kg(t.cap_kg)} kg`, (w / t.cap_kg) * 100, w / t.cap_kg > 0.9 ? "hi" : "")}
    </div>
  );
}

function LoadMap({ d, ov, nextLine }: { d: DockTrip; ov: Overlay; nextLine: number | null }) {
  const t = d.trip;
  const used = d.load_order.reduce((a, v) => a + v.m3, 0);
  const free = Math.max(0, t.cap_m3 - used);
  return (
    <div className="scard">
      <div className="eyebrow">{t.vehicle} · Load map</div>
      <div className="vmap" role="img" aria-label="Load map">
        <div className="vcab">Cab · first in</div>
        <div className="vbody">
          {d.load_order.map((v) => {
            const st = v.lines.map((l) => lineState(l, ov));
            const done = st.filter((s) => s === "on" || s === "part").length;
            const flag = st.includes("flag");
            const cur = v.lines.some((l) => l.id === nextLine);
            const cls = flag ? "flag" : done === v.lines.length ? "full" : cur ? "cur" : "";
            return (
              <div key={v.stop_id} className={`vseg ${cls}`} style={{ flex: `${Math.max(v.m3, 0.2)} 1 0` }}>
                <i style={{ width: `${(done / Math.max(1, v.lines.length)) * 100}%` }} />
                <b>{v.seq}</b>
                <span>{v.name}</span>
                <em>{f1(v.m3)} m³</em>
              </div>
            );
          })}
          {free > 0.05 ? (
            <div className="vseg free" style={{ flex: `${free} 1 0` }}>
              <span>Free {f1(free)} m³</span>
            </div>
          ) : null}
        </div>
        <div className="vdoor">
          <Icon name="door" />
          Doors · last in, first off
        </div>
      </div>
      <p className="vtip">
        <Icon name="info" />
        <span>
          <b>Load the last stop first.</b> Stop 1 goes in last and sits by the doors, so {first(t.driver)} never digs for a case.
        </span>
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ release */

function ReleaseView({ d, ov, crew }: DetailProps) {
  const now = useVirtualNow(15_000);
  const nav = useNavigate();
  const toast = useToast();
  const t = d.trip;
  const prog = progress(d.load_order, ov);
  const parts = d.load_order.flatMap((v) => v.lines).filter((l) => lineState(l, ov) === "part").length;
  const [temp, setTemp] = useState(3);
  const [seal, setSeal] = useState("");
  const [sealed, setSealed] = useState(false);
  const [photo, setPhoto] = useState<string | null>(null);
  const tempOk = !t.needs_temp || (temp >= 2 && temp <= 4);
  const can = tempOk && seal.trim().length >= 4 && sealed;
  const ready = prog.total > 0 && prog.done === prog.total && !prog.waiting;
  useEffect(() => {
    if (!ready) nav(`/dock/t/${t.id}`, { replace: true });
  }, [ready, nav, t.id]);
  const left = minutesBetween(now, t.depart);
  return (
    <section className="dpane">
      <div className="dscroll">
        <div className="rhead">
          <button className="backbtn" onClick={() => nav(`/dock/t/${t.id}`)} aria-label="Back">
            <Icon name="chevL" />
          </button>
          <div>
            <div className="eyebrow">
              {t.vehicle} · Bay {t.bay} · Departs {hm(t.depart)} · {left > 0 ? `in ${dur(left)}` : "now"}
            </div>
            <h1>Last check before {t.vehicle} leaves</h1>
          </div>
        </div>
        <div className="rgrid">
          <div className="stack">
            <div className="card">
              <div className="ch">
                <span className="ok">
                  <Icon name="check" />
                </span>
                <b>Load matches the plan</b>
              </div>
              <ul className="clist">
                <li>
                  <Icon name="check" />
                  {prog.done - parts} of {prog.total} lines complete
                </li>
                {parts ? (
                  <li className="amb">
                    <Icon name="split" />
                    {parts} partial line{parts > 1 ? "s" : ""}, approved by dispatch
                  </li>
                ) : null}
                <li>
                  <Icon name="check" />
                  Stops loaded last-first
                </li>
              </ul>
            </div>
            {t.needs_temp ? (
              <div className={`card ${tempOk ? "" : "ember"}`}>
                <div className="spread">
                  <div>
                    <b>Reefer temperature</b>
                    <div className="muted sm">Target 2–4 °C for dairy and meat</div>
                  </div>
                  <div className="stepper">
                    <button onClick={() => setTemp(Math.max(-2, temp - 1))} aria-label="Colder">
                      <Icon name="minus" />
                    </button>
                    <span>{temp} °C</span>
                    <button onClick={() => setTemp(Math.min(10, temp + 1))} aria-label="Warmer">
                      <Icon name="plus" />
                    </button>
                  </div>
                </div>
                {tempOk ? null : <p className="warnp">Outside the safe range. Adjust before release.</p>}
              </div>
            ) : (
              <div className="card soft row">
                <Icon name="info" />
                <span className="muted">Ambient load. No temperature check.</span>
              </div>
            )}
          </div>
          <div className="stack">
            <div className="card">
              <label className="flabel" htmlFor="seal">
                Seal number
              </label>
              <input
                id="seal"
                className="input"
                value={seal}
                onChange={(e) => setSeal(e.target.value.toUpperCase())}
                placeholder={`${t.dock === 1 ? "KH" : "PG"}-58213`}
                autoComplete="off"
                spellCheck={false}
                maxLength={20}
              />
            </div>
            <div className="card">
              <div className="spread">
                <div>
                  <b>Doors closed and sealed</b>
                  <div className="muted sm">Confirm you checked it yourself</div>
                </div>
                <button
                  className={`toggle ${sealed ? "on" : ""}`}
                  onClick={() => setSealed(!sealed)}
                  role="switch"
                  aria-checked={sealed}
                  aria-label="Doors closed and sealed"
                />
              </div>
            </div>
            <PhotoInput value={photo} onChange={setPhoto} label="Photo of the load · optional" />
          </div>
        </div>
        <div className="handover">
          <span className="av" style={{ background: "var(--ember)" }}>
            {initials(t.driver)}
          </span>
          <span>
            Releasing puts the run on {first(t.driver)}'s phone and shows {t.vehicle} as loaded to dispatch.
          </span>
        </div>
      </div>
      <div className="dfoot">
        <Swipe
          label={`Swipe to release ${t.vehicle}`}
          locked={!can}
          lockedLabel="Temperature, seal and doors first"
          onDone={async () => {
            await record(
              "trip.release",
              { trip_id: t.id, temp_c: t.needs_temp ? temp : null, seal: seal.trim(), doors_ok: true, photo, crew },
              `Released ${t.vehicle} · seal ${seal.trim()}`,
            );
            toast(`${t.vehicle} released. The run is on ${first(t.driver)}'s phone.`, "green", "check");
            nav(`/dock/t/${t.id}`, { replace: true });
          }}
        />
      </div>
    </section>
  );
}

function ReleasedView({ d, q, ov }: DetailProps) {
  const nav = useNavigate();
  const t = d.trip;
  const rel = ov.releases.get(t.id);
  const at = t.released_at ?? rel?.at ?? null;
  const by = t.released_by ?? rel?.crew ?? "";
  const seal = t.seal ?? rel?.seal ?? "";
  const stops = [...d.load_order].sort((a, b) => a.seq - b.seq);
  const s1 = stops[0];
  const flagged = d.issues.filter((i) => i.kind.endsWith("_at_dock"));
  const lines = new Map(d.load_order.flatMap((v) => v.lines.map((l) => [l.id, { l, v }] as const)));
  const next = q.trips.find((x) => x.id !== t.id && !isReleased(x.status) && !ov.releases.has(x.id));
  const early = at ? minutesBetween(at, t.depart) : 0;
  return (
    <section className="dpane">
      <div className="dscroll">
        <div className="done-hero">
          <div className="big">
            <Icon name="check" />
          </div>
          <h1>{t.vehicle} released</h1>
          <p>
            {at ? `${hm(at)} · ${early > 0 ? `${dur(early)} before departure` : "at departure"}` : ""}
            {by ? ` · by ${by}` : ""}
            {seal ? ` · seal ${seal}` : ""}
            {t.status === "out" ? " · on the road now" : ""}
          </p>
        </div>
        <div className="rel-rows">
          <div className="rel-row">
            <span className="ri">
              <Icon name="mobile" />
            </span>
            <div>
              <b>{t.driver}'s phone</b>
              <span>
                Run loaded: {stops.length} stop{stops.length === 1 ? "" : "s"}
                {s1 ? `, first ${s1.name} about ${hm(s1.eta)}` : ""}.
              </span>
            </div>
          </div>
          <div className="rel-row">
            <span className="ri">
              <Icon name="layers" />
            </span>
            <div>
              <b>{q.dispatcher}'s board</b>
              <span>
                {t.vehicle} shows as loaded.{flagged.length ? ` Approved shortfalls: ${flagged.length}.` : ""}
              </span>
            </div>
          </div>
          {flagged.map((i) => {
            const x = lines.get(i.line ?? -1);
            if (!x) return null;
            return (
              <div key={i.id} className="rel-row">
                <span className="ri">
                  <Icon name="store" />
                </span>
                <div>
                  <b>
                    {x.v.manager ? `${first(x.v.manager)} · ` : ""}
                    {x.v.name}
                  </b>
                  <span>
                    Told: {x.l.qty - (i.qty ?? 0)} of {x.l.qty} {x.l.name} today, {i.qty} on the next run.
                  </span>
                </div>
              </div>
            );
          })}
        </div>
        <div className="rel-acts">
          {next ? (
            <button className="btn primary lg" onClick={() => nav(`/dock/t/${next.id}`)}>
              Next: {next.vehicle} · Trip {next.trip_no} · {hm(next.depart)} <Icon name="arrowR" />
            </button>
          ) : (
            <span className="chip green lg">
              <Icon name="check" /> Every truck on this dock is released
            </span>
          )}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ overlays */

function CrewDialog({ q, crew, onPick, onClose }: { q: DockQueue; crew: Crew | null; onPick: (c: Crew) => void; onClose: () => void }) {
  const now = useVirtualNow(30_000);
  return (
    <>
      <div className={`backdrop ${crew ? "" : "solid"}`} onClick={onClose} />
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="crewT">
        <div className="dlg-top">
          <Logo size={40} />
          <div style={{ flex: 1 }}>
            <div className="eyebrow">
              {q.depot_name} · Dock {q.dock} · {hm(now)}
            </div>
            <h2 id="crewT">Who's loading?</h2>
          </div>
          {crew ? (
            <button className="xbtn" onClick={onClose} aria-label="Close">
              <Icon name="x" />
            </button>
          ) : null}
        </div>
        <p className="muted">This is the shared dock tablet. Tap your name. Every line you tick carries it.</p>
        <div className="crew">
          {q.crew_roster.map((c) => (
            <button key={c.short} className={`ctile ${crew?.short === c.short ? "on" : ""}`} onClick={() => onPick(c)}>
              <span className="av lg" style={{ background: c.color }}>
                {c.initials}
              </span>
              <div>
                <b>{c.name}</b>
                <small>Bay {c.bay}</small>
              </div>
            </button>
          ))}
        </div>
        <p className="fine">
          <Icon name="lock" />
          No password per person on the dock tablet. {q.dispatcher} sees who ticked each line.
        </p>
      </div>
    </>
  );
}

function ChangesDrawer({
  q,
  changes,
  onClose,
  onOpen,
  onAck,
}: {
  q: DockQueue;
  changes: Notice[];
  onClose: () => void;
  onOpen: (tripId: number) => void;
  onAck: (n: Notice) => void;
}) {
  const trips = new Map(q.trips.map((t) => [t.id, t]));
  const others = q.changes.filter((n) => !isChange(n)).slice(0, 8);
  const label = (n: Notice) => {
    const t = trips.get(tripOf(n) ?? -1);
    return t ? `${t.vehicle} · Trip ${t.trip_no} · Bay ${t.bay} · departs ${hm(t.depart)}` : "";
  };
  return (
    <Drawer
      title="What changed"
      sub={q.plan ? `Plan v${q.plan.version} from ${q.dispatcher}, published ${hm(q.plan.published_at)}.` : undefined}
      onClose={onClose}
    >
      {changes.length === 0 ? (
        <p className="nochg">
          <Icon name="check" />
          No changes since the plan was published.
        </p>
      ) : null}
      {changes.map((n) => {
        const k = changeKind(n);
        const tid = tripOf(n);
        if (n.read)
          return (
            <div key={n.id} className="pc done">
              <span className="ok">
                <Icon name="check" />
              </span>
              <div>
                <b>{k === "take" ? n.title.replace(/^(Unseal and take|Take) off/, "Taken off") : n.title}</b>
                <span>Confirmed · {hm(n.at)}</span>
              </div>
            </div>
          );
        return (
          <div key={n.id} className={`pc ${k === "take" ? "ember" : "blue"}`}>
            <div className="pch">
              <span className={`chip ${k === "take" ? "ember" : "blue"}`}>
                <Icon name={k === "take" ? "minus" : k === "new" ? "plus" : "layers"} />
                {k === "take" ? "Take off" : k === "new" ? "New" : "Changed"}
              </span>
              <span className="mono">{label(n)}</span>
            </div>
            <h3>{n.title}</h3>
            <p>{n.body}</p>
            {k === "take" ? (
              <p className="taketip">
                <Icon name="info" />
                <span>Loaded early, so it sits towards the cab. Pull the stops in front of it forward to reach it.</span>
              </p>
            ) : null}
            <div className="row">
              <button className={`btn ${k === "take" ? "ember" : "primary"}`} onClick={() => onAck(n)}>
                <Icon name="check" />
                {k === "take" ? "It's off the truck" : "Got it"}
              </button>
              {tid && trips.has(tid) ? (
                <button className="btn ghost" onClick={() => onOpen(tid)}>
                  Open {trips.get(tid)!.vehicle} · T{trips.get(tid)!.trip_no}
                </button>
              ) : null}
            </div>
          </div>
        );
      })}
      {others.length ? (
        <div className="chg-log">
          <div className="eyebrow">Earlier tonight</div>
          {others.map((n) => (
            <div key={n.id} className="chg-row">
              <Icon name={n.icon || "info"} />
              <div>
                <b>{n.title}</b>
                {n.body} <span className="muted">· {hm(n.at)}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </Drawer>
  );
}

function FlagSheet({
  d,
  line,
  visit,
  dispatcher,
  onClose,
  onSend,
}: {
  d: DockTrip;
  line: Line;
  visit: DockVisit;
  dispatcher: string;
  onClose: () => void;
  onSend: (kind: string, qty: number, note: string, photo: string | null) => Promise<void>;
}) {
  const [kind, setKind] = useState("missing");
  const [qty, setQty] = useState(Math.min(3, line.qty));
  const [photo, setPhoto] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const need = kind === "damaged" && !photo;
  const notes = ["Not on the pick shelf", "Crushed case", "Leaking"];
  return (
    <Drawer
      title={`Problem with ${line.name}`}
      sub={
        <>
          <span className="eyebrow" style={{ display: "block", marginBottom: 4 }}>
            {d.trip.vehicle} · {visit.name} · Stop {visit.seq}
          </span>
          {line.qty} cs ordered
        </>
      }
      onClose={onClose}
      foot={
        <button
          className="btn ember block lg"
          disabled={need || busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onSend(kind, qty, note ?? "", photo);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Icon name="flag" />
          Flag and tell {dispatcher}
        </button>
      }
    >
      <div className="typeseg">
        {[
          ["missing", "box", "Missing"],
          ["damaged", "alert", "Damaged"],
          ["wrong", "ban", "Wrong item"],
        ].map(([k, ic, label]) => (
          <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)} aria-pressed={kind === k}>
            <Icon name={ic} />
            <span>{label}</span>
          </button>
        ))}
      </div>
      <div className="qbox">
        <div>
          <b>How many cases are short?</b>
          <small>
            {line.qty - qty} of {line.qty} can go on the truck
          </small>
        </div>
        <div className="stepper">
          <button onClick={() => setQty(Math.max(1, qty - 1))} aria-label="Fewer">
            <Icon name="minus" />
          </button>
          <span>{qty}</span>
          <button onClick={() => setQty(Math.min(line.qty, qty + 1))} aria-label="More">
            <Icon name="plus" />
          </button>
        </div>
      </div>
      {line.qty <= 120 ? (
        <div className="cases" aria-hidden="true">
          {Array.from({ length: line.qty }, (_, i) => (
            <i key={i} className={i >= line.qty - qty ? "s" : ""} />
          ))}
        </div>
      ) : null}
      <div className="dsec">
        <PhotoInput
          value={photo}
          onChange={setPhoto}
          label={kind === "damaged" ? "Add a photo (needed for damage)" : "Add a photo (optional)"}
          need={kind === "damaged"}
        />
      </div>
      <div className="dsec">
        <span className="flabel">Quick note</span>
        <div className="nchips">
          {notes.map((n) => (
            <button key={n} className={`nchip ${note === n ? "on" : ""}`} onClick={() => setNote(note === n ? null : n)}>
              {n}
            </button>
          ))}
        </div>
      </div>
      <div className="whod">
        <Icon name="info" />
        <span>
          {dispatcher} decides: send what is loaded, hold the truck, or re-order the cases for tomorrow. {first(d.trip.driver)} and the store hear her decision
          automatically.
        </span>
      </div>
    </Drawer>
  );
}
