/**
 * Driver face: the run on a phone, built to keep working without signal.
 *
 *   Run     waiting for the dock → start → next stop → arrive → unload → proof → next … → done
 *   Report  one-tap problem reports to dispatch
 *   Sync    what is saved on the phone, what reached the server, and what dispatch changed meanwhile
 *   Me      language (English, Sinhala, Tamil), night mode, test offline mode, sign out
 *
 * Offline-first: every action is written to IndexedDB with the device's (virtual) time and sent by
 * the outbox. The screen always shows the last server view with the phone's own pending records
 * laid over it, so a driver in a dead zone sees exactly what they did.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router";
import { api, errorText, get, post } from "../../lib/api";
import { signOut, useMe } from "../../lib/auth";
import { parseNaive, setClock, useClockState, useVirtualNow, virtualNow } from "../../lib/clock";
import { dayLabel, f1, hm, kg, sameDayAt } from "../../lib/format";
import { pinCheck, pinProof } from "../../lib/handover";
import { useLive, useLiveStatus } from "../../lib/live";
import { cacheGet, cachePut, type OutboxItem } from "../../lib/offline/db";
import { clearSynced, flush, onSynced, record, setSimulatedOffline, useConnectivity, useOutbox, usePending, useSyncLoop } from "../../lib/offline/outbox";
import type { Line, Notice, Run, RunTrip, Visit } from "../../lib/types";
import { Icon } from "../../ui/Icon";
import { PhotoInput, Swipe, ToastProvider, useToast, WindowBar } from "../../ui/kit";
import { I18n, LANGS, makeT, useT, type Lang, type T } from "./i18n";
import "../../ui/ds.css";
import "./driver.css";

const THEME_KEY = "relay.driver.theme";
const LANG_KEY = "relay.driver.lang";
const CHANGES_KEY = "relay.driver.changes";

function readLS<T>(k: string, d: T): T {
  try {
    const v = localStorage.getItem(k);
    return v ? (JSON.parse(v) as T) : d;
  } catch {
    return d;
  }
}
function writeLS(k: string, v: unknown) {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {
    /* ignore */
  }
}

/* ------------------------------------------------------------------ app shell */

export default function DriverApp() {
  const me = useMe().data!;
  const [lang, setLangState] = useState<Lang>(() => readLS<Lang | null>(LANG_KEY, null) ?? ((me.lang as Lang) || "en"));
  const [dark, setDark] = useState<boolean>(() => readLS<string>(THEME_KEY, "light") === "dark");
  const { online } = useConnectivity();
  useLive([["driver"]], online);
  useSyncLoop(true);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0B0F1C" : "#FFFFFF");
    writeLS(THEME_KEY, dark ? "dark" : "light");
  }, [dark]);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const setLang = (l: Lang) => {
    setLangState(l);
    writeLS(LANG_KEY, l);
    void api("/api/auth/me", { method: "PATCH", body: { lang: l } }).catch(() => undefined);
  };
  const i18n = useMemo(() => ({ lang, t: makeT(lang) }), [lang]);
  return (
    <I18n.Provider value={i18n}>
      <ToastProvider>
        <Shell dark={dark} setDark={setDark} setLang={setLang} />
      </ToastProvider>
    </I18n.Provider>
  );
}

function Shell({ dark, setDark, setLang }: { dark: boolean; setDark: (v: boolean) => void; setLang: (l: Lang) => void }) {
  const { t, lang } = useT();
  const data = useRunData();
  const pending = usePending();
  const qc = useQueryClient();
  const toast = useToast();
  // results of each sync: rejected records and dispatch changes made while the phone was dark
  useEffect(
    () =>
      onSynced((r) => {
        qc.invalidateQueries({ queryKey: ["driver"] });
        const notes = [
          ...r.changes.map((c) => ({ text: c, kind: "blue" })),
          ...r.results.filter((x) => x.status === "rejected").map((x) => ({ text: x.message ?? "A record was not applied.", kind: "ember" })),
        ];
        if (notes.length) {
          const prev = readLS<{ text: string; kind: string; at: string }[]>(CHANGES_KEY, []);
          writeLS(CHANGES_KEY, [...notes.map((n) => ({ ...n, at: r.clock.now })), ...prev].slice(0, 12));
          window.dispatchEvent(new Event("relay:changes"));
          toast(notes[0].text, notes[0].kind === "ember" ? "ember" : "blue", "sync");
        }
      }),
    [qc, toast],
  );
  const tabs: [string, string, string, number?][] = [
    ["/driver", "route", t("tab_run")],
    ["/driver/report", "flag", t("tab_report")],
    ["/driver/sync", "sync", t("tab_sync"), pending],
    ["/driver/me", "user", t("tab_me")],
  ];
  return (
    <div className="drv" lang={lang}>
      <Routes>
        <Route index element={<RunTab data={data} dark={dark} />} />
        <Route path="report" element={<ReportTab data={data} />} />
        <Route path="sync" element={<SyncTab data={data} />} />
        <Route path="me" element={<MeTab data={data} dark={dark} setDark={setDark} setLang={setLang} />} />
        <Route path="*" element={<Navigate to="/driver" replace />} />
      </Routes>
      <nav className="tabbar" aria-label="Driver">
        {tabs.map(([to, icon, label, badge]) => (
          <NavLink key={to} to={to} end={to === "/driver"} className={({ isActive }) => (isActive ? "on" : "")}>
            <Icon name={icon} />
            {label}
            {badge ? <span className="badge">{badge}</span> : null}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

/* ------------------------------------------------------------------ data: server view + phone overlay */

type RunData = { run: Run | null; loading: boolean; error: unknown; fromCache: boolean };

function useRunData(): RunData {
  const live = useLiveStatus();
  const { online } = useConnectivity();
  const q = useQuery<Run>({
    queryKey: ["driver", "run"],
    queryFn: async () => {
      const r = await get<Run>("/api/driver/run");
      void cachePut("driver.run", r);
      return r;
    },
    enabled: online,
    refetchInterval: live ? false : 20_000,
  });
  const [cached, setCached] = useState<{ run: Run; at: number } | null>(null);
  useEffect(() => {
    void cacheGet<Run>("driver.run").then((r) => r && setCached({ run: r, at: Date.now() - 60_000 }));
  }, []);
  useEffect(() => {
    if (q.data) setClock(q.data.clock);
  }, [q.data]);
  const items = useOutbox();
  const base = q.data ?? cached?.run ?? null;
  const since = q.data ? q.dataUpdatedAt : (cached?.at ?? 0);
  const run = useMemo(() => (base ? overlay(base, items, since) : null), [base, items, since]);
  return { run, loading: q.isLoading && !cached, error: q.error, fromCache: !q.data && !!cached };
}

function findVisit(r: Run, stopId: number): { trip: RunTrip; visit: Visit } | null {
  for (const trip of r.trips) for (const visit of trip.visits) if (visit.stop_ids.includes(stopId)) return { trip, visit };
  return null;
}

const DONE = ["delivered", "partial", "failed"];

function overlay(run: Run, items: OutboxItem[], since: number): Run {
  const fresh = items
    .filter((it) => it.status === "pending" || (it.status === "synced" && (it.syncedAt ?? 0) > since))
    .sort((a, b) => a.createdAt - b.createdAt);
  if (!fresh.length) return run;
  const r: Run = structuredClone(run);
  for (const it of fresh) {
    const p = it.payload as Record<string, never>;
    if (it.type === "run.start") {
      const t = r.trips.find((x) => x.id === p.trip_id);
      if (t && ["planned", "loading", "loaded"].includes(t.status)) {
        t.status = "out";
        t.departed_at = t.departed_at ?? it.at;
      }
    } else if (it.type === "stop.arrive") {
      const f = findVisit(r, (p.stop_ids as number[])[0]);
      if (f && f.visit.status === "pending") {
        f.visit.status = "arrived";
        f.visit.arrived_at = it.at;
        f.visit.eta = it.at;
        f.visit.eta_kind = "actual";
        if (f.trip.status !== "out" && f.trip.status !== "done") f.trip.status = "out";
      }
    } else if (it.type === "stop.complete") {
      const f = findVisit(r, (p.stop_ids as number[])[0]);
      if (f && (f.visit.status === "pending" || f.visit.status === "arrived")) {
        const v = f.visit;
        v.status = p.outcome;
        v.arrived_at = v.arrived_at ?? it.at;
        v.completed_at = it.at;
        v.proof = {
          receiver: p.receiver ?? "",
          photo: p.photo ?? null,
          signature: null,
          pin_verified: !!p.pin_proof,
          at: it.at,
          note: p.note ?? "",
          simulated: false,
          offline: it.offline,
          synced_at: it.syncedVirtual ?? null,
        };
        const qty = new Map(((p.lines ?? []) as { line_id: number; delivered_qty: number }[]).map((x) => [x.line_id, x.delivered_qty]));
        v.orders.forEach((o) => o.lines.forEach((l) => (l.delivered_qty = qty.get(l.id) ?? l.delivered_qty)));
        if (f.trip.status !== "out") f.trip.status = "out";
        if (f.trip.visits.every((x) => DONE.includes(x.status))) f.trip.status = "done";
      }
    }
  }
  return r;
}

/* ------------------------------------------------------------------ shared bits */

function ConnPill() {
  const { t } = useT();
  const { online } = useConnectivity();
  const pending = usePending();
  if (!online)
    return (
      <span className="connpill off">
        <Icon name="wifiOff" />
        {pending ? t("offlineN", { n: pending }) : t("offline")}
      </span>
    );
  if (pending)
    return (
      <span className="connpill wait">
        <Icon name="sync" />
        {t("syncing", { n: pending })}
      </span>
    );
  return (
    <span className="connpill">
      <Icon name="check" />
      {t("online")}
    </span>
  );
}

function OfflineBanner() {
  const { t } = useT();
  const { online } = useConnectivity();
  if (online) return null;
  return (
    <div className="offbanner" role="status">
      <Icon name="wifiOff" />
      <div>
        <b>{t("offlineT")}</b>
        {t("offlineB")}
      </div>
    </div>
  );
}

function vehicleName(t: T, type?: string, temp?: string) {
  if (temp === "reefer") return type === "van" ? t("reeferVan") : t("reeferTruck");
  return type === "van" ? t("van") : t("truck");
}

function greeting(t: T, ms: number, name: string) {
  const h = new Date(ms).getUTCHours();
  return t(h >= 3 && h < 12 ? "morning" : h >= 12 && h < 17 ? "afternoon" : h >= 17 ? "evening" : "morning", { name });
}

function RouteMap({ trip, curIdx, dark, offline }: { trip: RunTrip; curIdx: number; dark: boolean; offline: boolean }) {
  const n = trip.visits.length;
  const pts = trip.visits.map((_, i) => {
    const f = (i + 1) / (n + 1);
    return { x: 40 + f * 300 + Math.sin(i * 1.7) * 16, y: 178 - f * 140 + Math.cos(i * 1.3) * 18 };
  });
  const dep = { x: 30, y: 190 };
  const path = [dep, ...pts].map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(0)},${p.y.toFixed(0)}`).join(" ");
  const started = trip.status === "out" || trip.status === "done";
  const cur =
    curIdx < 0
      ? (pts[n - 1] ?? dep)
      : curIdx === 0
        ? started
          ? { x: (dep.x + pts[0].x) / 2, y: (dep.y + pts[0].y) / 2 }
          : dep
        : trip.visits[curIdx].status === "arrived"
          ? pts[curIdx]
          : { x: (pts[curIdx - 1].x + pts[curIdx].x) / 2, y: (pts[curIdx - 1].y + pts[curIdx].y) / 2 };
  const land = dark ? "#121A2E" : "#E3EAF7";
  const road = dark ? "#26314D" : "#FFFFFF";
  const water = dark ? "#0D1730" : "#CFE0F7";
  return (
    <svg viewBox="0 0 380 210" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <rect width="380" height="210" fill={land} />
      <path d="M0 30 C80 60 120 10 200 40 S320 20 380 50 V0 H0z" fill={water} />
      <path d="M-10 160 C60 140 90 180 160 150 S300 100 390 130" stroke={road} strokeWidth="10" fill="none" opacity=".75" />
      <path d="M60 230 C90 170 150 140 170 80 S230 10 260 -10" stroke={road} strokeWidth="7" fill="none" opacity=".6" />
      <path d="M250 230 C260 180 300 150 390 170" stroke={road} strokeWidth="6" fill="none" opacity=".5" />
      <path d={path} stroke={dark ? "#05070F" : "#FFFFFF"} strokeWidth="9" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <path d={path} stroke="#1F4BFF" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <rect x={dep.x - 9} y={dep.y - 9} width="18" height="18" rx="5" fill={dark ? "#FFFFFF" : "#0B0D12"} />
      {pts.map((p, i) => {
        const v = trip.visits[i];
        const done = DONE.includes(v.status);
        const bad = v.status === "failed";
        const isCur = i === curIdx;
        const fill = bad ? "#F0512A" : done ? "#0F9D6B" : isCur ? "#1F4BFF" : dark ? "#05070F" : "#FFFFFF";
        return (
          <g key={v.visit_id}>
            <circle cx={p.x} cy={p.y} r="10" fill={fill} stroke={bad ? "#F0512A" : done ? "#0F9D6B" : "#1F4BFF"} strokeWidth="3" />
            <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize="10" fontWeight="800" fill={done || isCur ? "#fff" : "#1F4BFF"} fontFamily="Archivo,Arial">
              {i + 1}
            </text>
          </g>
        );
      })}
      {started && curIdx >= 0 ? (
        <>
          <circle cx={cur.x} cy={cur.y} r="18" fill={offline ? "rgba(240,81,42,.25)" : "rgba(31,75,255,.22)"}>
            <animate attributeName="r" values="12;20;12" dur="2s" repeatCount="indefinite" />
          </circle>
          <circle cx={cur.x} cy={cur.y} r="8" fill={offline ? "#F0512A" : "#1F4BFF"} stroke="#fff" strokeWidth="3" />
        </>
      ) : null}
    </svg>
  );
}

function StopList({ trip, curIdx }: { trip: RunTrip; curIdx: number }) {
  const { t } = useT();
  const items = useOutbox();
  return (
    <div className="stoplist">
      {trip.visits.map((v, i) => {
        const done = DONE.includes(v.status);
        const onPhone = items.some((it) => it.status === "pending" && it.type === "stop.complete" && (it.payload.stop_ids as number[])?.[0] === v.stop_ids[0]);
        const label =
          v.status === "delivered"
            ? t("delivered")
            : v.status === "partial"
              ? t("partial")
              : v.status === "failed"
                ? v.proof?.note?.startsWith("Refused")
                  ? t("refused")
                  : t("closed")
                : "";
        return (
          <div key={v.visit_id} className={`stop ${done ? (v.status === "failed" ? "bad" : "done") : i === curIdx ? "cur" : ""}`}>
            <div className="rail">
              <i />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="nm">{v.name}</div>
              <div className="meta">
                {done
                  ? `${label} ${hm(v.completed_at)}${onPhone ? ` · ${t("onPhone")}` : ""}`
                  : v.status === "arrived"
                    ? t("arrivedAt", { t: hm(v.arrived_at) })
                    : `~${hm(v.eta)} · ${t("window").toLowerCase()} ${v.window[0]}–${v.window[1]}`}
              </div>
            </div>
            {v.orders.some((o) => o.temp === "chilled") ? (
              <span className="snow">
                <Icon name="snow" />
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function News({ run }: { run: Run }) {
  const { t } = useT();
  const qc = useQueryClient();
  const n: Notice | undefined = run.notices.find((x) => !x.read);
  if (!n) return null;
  return (
    <div className={`news ${n.kind === "amber" ? "amber" : n.kind === "ember" ? "ember" : ""}`}>
      <Icon name={n.icon || "info"} />
      <div>
        <div className="eyebrow">
          {t("newsT")} · {hm(n.at)}
        </div>
        <b>{n.title}</b>
        {n.body ? <p>{n.body}</p> : null}
      </div>
      <button
        onClick={() =>
          post("/api/driver/notices/read")
            .then(() => qc.invalidateQueries({ queryKey: ["driver"] }))
            .catch(() => undefined /* offline: the notice stays until the next tap with signal */)
        }
      >
        {t("gotIt")}
      </button>
    </div>
  );
}

function shortfalls(trip: RunTrip): { line: Line; seq: number }[] {
  const out: { line: Line; seq: number }[] = [];
  trip.visits.forEach((v) =>
    v.orders.forEach((o) => o.lines.forEach((l) => l.flag && l.flag.status !== "open" && l.flag.decision !== "hold" && out.push({ line: l, seq: v.seq }))),
  );
  return out;
}

/* ------------------------------------------------------------------ Run tab */

function RunTab({ data, dark }: { data: RunData; dark: boolean }) {
  const { t } = useT();
  const { run } = data;
  if (data.loading)
    return (
      <div className="drv-pad">
        <div className="skeleton" style={{ height: 210 }} />
        <div className="skeleton" style={{ height: 160 }} />
      </div>
    );
  if (!run)
    return (
      <div className="drv-pad">
        <div className="empty">
          <Icon name="alert" />
          <b>{t("noRun")}</b>
          <span>{data.error ? errorText(data.error) : ""}</span>
        </div>
      </div>
    );
  if (!run.trips.length)
    return (
      <>
        <div className="apphead">
          <div className="greet">{greeting(t, virtualNow(), run.driver.short)}</div>
          <ConnPill />
        </div>
        <div className="drv-pad">
          <News run={run} />
          <div className="card empty">
            <Icon name="cal" />
            <b>{run.pending_plan && run.plan === null ? t("planning", { name: run.dispatcher }) : t("noRun")}</b>
            <span>{t("noRunSub")}</span>
          </div>
        </div>
      </>
    );
  const active = run.trips.find((x) => x.status !== "done") ?? null;
  const later = active ? run.trips.filter((x) => x.trip_no > active.trip_no && x.status !== "done") : [];
  if (!active) return <RunDone run={run} trip={run.trips[run.trips.length - 1]} />;
  if (["planned", "loading", "loaded"].includes(active.status)) {
    const prev = run.trips.filter((x) => x.status === "done");
    return <Home run={run} trip={active} dark={dark} later={later} prevDone={prev.length > 0} />;
  }
  const arrivedIdx = active.visits.findIndex((v) => v.status === "arrived");
  if (arrivedIdx >= 0) return <Arrived key={active.visits[arrivedIdx].visit_id} run={run} trip={active} idx={arrivedIdx} />;
  const nextIdx = active.visits.findIndex((v) => v.status === "pending");
  if (nextIdx >= 0) return <EnRoute run={run} trip={active} idx={nextIdx} dark={dark} />;
  return <RunDone run={run} trip={active} />;
}

function Home({ run, trip, dark, later, prevDone }: { run: Run; trip: RunTrip; dark: boolean; later: RunTrip[]; prevDone: boolean }) {
  const { t } = useT();
  const now = useVirtualNow(30_000);
  const released = trip.status === "loaded";
  const loading = trip.status === "loading";
  const cases = trip.visits.reduce((a, v) => a + v.orders.reduce((b, o) => b + o.units, 0), 0);
  const last = trip.visits[trip.visits.length - 1];
  const sf = shortfalls(trip);
  let msg: string;
  if (released)
    msg =
      t("releasedMsg", { who: trip.released_by ?? "the dock", t: hm(trip.released_at), seal: trip.seal ?? "—" }) +
      " " +
      (sf.length
        ? t("shortfallsMsg", {
            list: sf.map((x) => `${x.line.flag?.qty} × ${x.line.name} (${t("stopOf", { i: x.seq, n: trip.visits.length }).toLowerCase()})`).join(", "),
          })
        : t("nothingMissing"));
  else if (loading || trip.lines_done > 0)
    msg = t("loadingMsg", { who: trip.loader ?? "The dock", dock: trip.dock, done: trip.lines_done, total: trip.lines_total });
  else msg = t("notLoadingMsg", { t: hm(parseNaive(trip.depart) - 75 * 60000) });
  return (
    <>
      <div className="map">
        <RouteMap trip={trip} curIdx={-1} dark={dark} offline={false} />
        <div className="maptop">
          <ConnPill />
          <span className="chip">
            <Icon name="clock" />
            {hm(now)}
          </span>
          <span className={`chip ${released ? "green" : loading ? "amber" : ""}`}>
            <Icon name={released ? "check" : "box"} />
            {released ? t("released") : loading ? t("loading") : t("planned")}
          </span>
        </div>
      </div>
      <OfflineBanner />
      <div className="drv-pad">
        <div>
          <div className="greet">{greeting(t, now, run.driver.short)}</div>
          <p className="sub">
            {run.depot_name} → {trip.district} · {dayLabel(run.clock.service_date)}
          </p>
        </div>
        <News run={run} />
        {prevDone ? (
          <div className="news">
            <Icon name="check" />
            <div>
              <b>
                {t("runDone")} · trip {trip.trip_no - 1}
              </b>
              <p>{t("backToDepot", { depot: run.depot_name, n: trip.trip_no, t: hm(trip.depart) })}</p>
            </div>
          </div>
        ) : null}
        <div className="row" style={{ flexWrap: "wrap" }}>
          <span className="vchip">
            <Icon name={run.vehicle?.type === "van" ? "van" : "truck"} />
            {run.vehicle?.id} · {vehicleName(t, run.vehicle?.type, run.vehicle?.temp)}
          </span>
          <span className="vchip">
            <Icon name="thermo" />
            {trip.temp_c !== null && trip.temp_c !== undefined ? `${trip.temp_c} °C` : "–"}
          </span>
        </div>
        <div className="card">
          <div className="spread">
            <h3>{trip.trip_no > 1 ? `${t("todayRun")} · ${trip.trip_no}` : t("todayRun")}</h3>
            <span className="chip blue">{t("stopsN", { n: trip.visits.length })}</span>
          </div>
          <div className="kv">
            <div>
              <b>{cases}</b>
              <small>{t("cases")}</small>
            </div>
            <div>
              <b>{hm(trip.depart)}</b>
              <small>{t("plannedStart")}</small>
            </div>
            <div>
              <b>{hm(last?.eta)}</b>
              <small>{t("lastStop")}</small>
            </div>
          </div>
          <p className="msg">{msg}</p>
        </div>
        {released ? (
          <button
            className="btn primary block bigbtn"
            onClick={() => void record("run.start", { trip_id: trip.id }, `Started trip ${trip.trip_no} · ${trip.district}`, parseNaive(trip.depart))}
          >
            <Icon name="arrowR" />
            {t("startRun")}
          </button>
        ) : (
          <button className="btn block bigbtn" disabled>
            <Icon name="clock" />
            {t("waitingRelease")}
          </button>
        )}
        <div className="card soft">
          <div className="eyebrow">{t("stops")}</div>
          <StopList trip={trip} curIdx={-1} />
        </div>
        {later.map((x) => (
          <p key={x.id} className="muted sm" style={{ textAlign: "center" }}>
            {t("laterTrip", { n: x.trip_no, t: hm(x.depart) })}
          </p>
        ))}
      </div>
    </>
  );
}

function EnRoute({ run, trip, idx, dark }: { run: Run; trip: RunTrip; idx: number; dark: boolean }) {
  const { t } = useT();
  const nav = useNavigate();
  const now = useVirtualNow(10_000);
  const { online } = useConnectivity();
  const v = trip.visits[idx];
  const lines = v.orders.flatMap((o) => o.lines);
  const units = v.orders.reduce((a, o) => a + o.units, 0);
  const chilled = v.orders.some((o) => o.temp === "chilled");
  const shorts = lines.filter((l) => l.flag && l.flag.status !== "open" && l.flag.decision !== "hold");
  return (
    <>
      <div className="map">
        <RouteMap trip={trip} curIdx={idx} dark={dark} offline={!online} />
        <div className="maptop">
          <ConnPill />
          <span className="chip">
            <Icon name="clock" />
            {hm(now)}
          </span>
          <button className="chip" onClick={() => nav("/driver/report")}>
            <Icon name="flag" />
            {t("tab_report")}
          </button>
        </div>
      </div>
      <OfflineBanner />
      <div className="drv-pad">
        <News run={run} />
        <div className="nextstop">
          <div className="spread">
            <span className="eyebrow">
              {t("stopOf", { i: idx + 1, n: trip.visits.length })} · {t("next")}
            </span>
            <span className={`chip ${v.late_prob > 0.45 ? "ember" : "green"}`}>{v.late_prob > 0.45 ? t("tight") : t("onTime")}</span>
          </div>
          <div className="big">{v.name}</div>
          <p className="sub">
            {t((v.dock_type as "rear_dock") ?? "street")} · {t("linesN", { n: lines.length })} · {t("casesN", { n: units })}
            {chilled ? ` · ${t("chilled")}` : ""}
          </p>
          <div className="tt">
            <div>
              <div className="eyebrow">{t("arriveBy")}</div>
              <b>~{hm(v.eta)}</b>
            </div>
            <div style={{ textAlign: "right" }}>
              <div className="eyebrow">{t("window")}</div>
              <b>
                {v.window[0]}–{v.window[1]}
              </b>
            </div>
          </div>
          <WindowBar window={v.window} day={run.clock.service_date} bandLo={v.band_lo} bandHi={v.band_hi} now={now} risk={v.late_prob} estimate={!online} />
          {shorts.map((l) => (
            <div key={l.id} className="shortnote">
              <Icon name="box" />
              {t("loadedShort", { q: l.flag?.qty ?? 0, line: l.name })}
            </div>
          ))}
        </div>
        <div className="swipe-row">
          <Swipe label={t("swipeArrive")} onDone={() => void record("stop.arrive", { stop_ids: v.stop_ids }, `${v.name} · arrived`, parseNaive(v.eta))} />
        </div>
        <div className="card soft">
          <div className="eyebrow">{t("runList")}</div>
          <StopList trip={trip} curIdx={idx} />
        </div>
      </div>
    </>
  );
}

type Sel = "delivered" | "refused" | "closed";

function Arrived({ trip, idx }: { run: Run; trip: RunTrip; idx: number }) {
  const { t } = useT();
  const toast = useToast();
  const { online } = useConnectivity();
  const v = trip.visits[idx];
  const lines = v.orders.flatMap((o) => o.lines.map((l) => ({ ...l, temp: o.temp })));
  // counted out at the dock: the server delivers what was loaded, and the store counts in on Confirm receipt
  const loaded = (l: Line) => l.loaded_qty ?? l.qty;
  const [sel, setSel] = useState<Sel>("delivered");
  const [step, setStep] = useState<"unload" | "proof">("unload");
  const [photo, setPhoto] = useState<string | null>(null);
  // with a PIN handover the name is only asked for when the manager is not there, so it starts empty
  const [receiver, setReceiver] = useState(v.contact && !v.pin_required ? v.contact.split(" ")[0] : "");
  const [reason, setReason] = useState("");
  // the store manager's PIN lives here only while it is typed; a match keeps just the proof hash
  const [pin, setPin] = useState("");
  const [proof, setProof] = useState<string | null>(null);
  const [wrongPin, setWrongPin] = useState(false);
  const [misses, setMisses] = useState(0);
  const [withoutPin, setWithoutPin] = useState(false);
  const failed = sel === "refused" || sel === "closed";
  const pinPending = !failed && v.pin_required && !proof && !withoutPin;
  const can = !!photo && !pinPending;
  const outcomes: [Sel, string, string][] = [
    ["delivered", "check", t("delivered")],
    ["refused", "hand", t("refused")],
    ["closed", "door", t("closed")],
  ];
  const confirmPin = () => {
    const match = !!v.pin_check && pinCheck(v.visit_id, pin) === v.pin_check;
    const hash = match ? pinProof(v.visit_id, pin) : null;
    setPin(""); // the PIN never outlives the tap
    if (hash) setProof(hash);
    else {
      setWrongPin(true);
      setMisses((n) => n + 1);
    }
  };
  const complete = async () => {
    // the status the server derives from what the dock loaded, so the phone's overlay agrees with it
    const outcome = failed ? "failed" : lines.some((l) => loaded(l) < l.qty) ? "partial" : "delivered";
    const note = sel === "refused" ? `Refused${reason ? `: ${reason}` : ""}` : sel === "closed" ? `Store closed${reason ? `: ${reason}` : ""}` : reason;
    const units = v.orders.reduce((a, o) => a + o.units, 0);
    const serviceMin = Math.min(30, Math.round(8 + units / 8));
    const sendProof = failed ? null : proof;
    await record(
      "stop.complete",
      {
        stop_ids: v.stop_ids,
        visit_id: v.visit_id,
        outcome,
        receiver: failed ? "" : sendProof ? v.contact : receiver.trim(),
        photo,
        ...(sendProof ? { pin_proof: sendProof } : {}),
        note,
      },
      `${v.name} · ${(outcome === "partial" ? t("partial") : outcomes.find((o) => o[0] === sel)![2]).toLowerCase()}`,
      (v.arrived_at ? parseNaive(v.arrived_at) : virtualNow()) + serviceMin * 60000,
    );
    toast(online ? t("savedSent") : t("savedOffline"), "green", "check");
  };
  if (step === "proof")
    return (
      <>
        <div className="apphead">
          <div className="row">
            <button className="backbtn" onClick={() => setStep("unload")} aria-label={t("back")}>
              <Icon name="chevL" />
            </button>
            <div>
              <div className="eyebrow">
                {t("proof")} · {v.name}
              </div>
              <h1 style={{ fontSize: 23 }}>{sel === "closed" ? t("photoClosed") : sel === "refused" ? t("photoRefused") : t("photoProof")}</h1>
            </div>
          </div>
        </div>
        <OfflineBanner />
        <div className="drv-pad">
          <div className="photo-drv">
            <PhotoInput value={photo} onChange={setPhoto} label={t("takePhoto")} />
          </div>
          {!photo ? (
            <button className="upl" onClick={() => setPhoto(samplePhoto(v.name))}>
              <Icon name="camera" />
              {t("samplePhoto")}
            </button>
          ) : null}
          {failed ? (
            <div>
              <label className="flabel" htmlFor="reason">
                {t("addNote")}
              </label>
              <textarea id="reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("reasonPh")} />
            </div>
          ) : (
            <>
              {v.pin_required ? (
                <div className={`card pincard ${proof ? "ok" : ""}`}>
                  {proof ? (
                    <div className="pinok" role="status">
                      <Icon name="shield" />
                      <b>{t("pinOk", { name: v.contact || t("theManager") })}</b>
                    </div>
                  ) : (
                    <>
                      <h3>{t("handTitle", { name: v.contact ? v.contact.split(" ")[0] : t("theManager") })}</h3>
                      <p className="pinsub">{t("handSub")}</p>
                      <label className="flabel" htmlFor="pin">
                        {t("pinLabel")}
                      </label>
                      <div className="pinrow">
                        <input
                          id="pin"
                          className="input pin"
                          type="password"
                          inputMode="numeric"
                          pattern="[0-9]*"
                          maxLength={6}
                          autoComplete="off"
                          data-lpignore="true"
                          data-1p-ignore
                          aria-invalid={wrongPin}
                          value={pin}
                          onChange={(e) => {
                            setPin(e.target.value.replace(/\D/g, "").slice(0, 6));
                            setWrongPin(false);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && pin.length >= 4) confirmPin();
                          }}
                        />
                        <button className="btn primary" disabled={pin.length < 4} onClick={confirmPin}>
                          {t("pinConfirm")}
                        </button>
                      </div>
                      {wrongPin ? (
                        <p className="pinbad" role="alert">
                          {t("pinBad")}
                        </p>
                      ) : null}
                      {misses >= 3 && !withoutPin ? (
                        <button className="pinskip" onClick={() => setWithoutPin(true)}>
                          {t("pinSkip")}
                        </button>
                      ) : null}
                    </>
                  )}
                </div>
              ) : null}
              {!v.pin_required || (withoutPin && !proof) ? (
                <div>
                  <label className="flabel" htmlFor="recv">
                    {t("receivedBy")}
                  </label>
                  <input
                    id="recv"
                    className="input text"
                    value={receiver}
                    onChange={(e) => setReceiver(e.target.value)}
                    placeholder={t("receiverPh")}
                    autoComplete="off"
                  />
                </div>
              ) : null}
            </>
          )}
          <div className="swipe-row">
            <Swipe label={t("swipeComplete")} locked={!can} lockedLabel={!photo ? t("needPhoto") : t("needPin")} onDone={() => void complete()} />
          </div>
          <p className="fine">{online ? t("savedSent") : t("savedOffline")}</p>
        </div>
      </>
    );
  return (
    <>
      <div className="apphead">
        <div>
          <div className="eyebrow">
            {t("stopOf", { i: idx + 1, n: trip.visits.length })} · {t("arrivedAt", { t: hm(v.arrived_at) })}
          </div>
          <h1>{v.name}</h1>
        </div>
        <ConnPill />
      </div>
      <OfflineBanner />
      <div className="drv-pad">
        <div className="card">
          <h3>{t("receiptTitle")}</h3>
          <p className="receipt-sub">{t("receiptSub")}</p>
          {lines.map((l) => {
            const short = l.qty - loaded(l);
            return (
              <div key={l.id} className="lineitem">
                <div>
                  <b>{l.name}</b>
                  <small>
                    {t("ordered", { n: l.qty })}
                    {l.temp === "chilled" ? ` · ${t("chilled")}` : ""}
                  </small>
                  {short > 0 ? <small className="warn">{t("loadedOf", { m: loaded(l), n: l.qty, k: short })}</small> : null}
                </div>
              </div>
            );
          })}
        </div>
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            {t("outcome")}
          </div>
          <div className="outcomes">
            {outcomes.map(([k, ic, label]) => (
              <button
                key={k}
                className={`${sel === k ? "on" : ""} ${k === "refused" || k === "closed" ? "bad" : ""}`}
                onClick={() => setSel(k)}
                aria-pressed={sel === k}
              >
                <Icon name={ic} />
                {label}
              </button>
            ))}
          </div>
        </div>
        <button className="btn primary block bigbtn" onClick={() => setStep("proof")}>
          {t("nextProof")} <Icon name="arrowR" />
        </button>
      </div>
    </>
  );
}

function RunDone({ run, trip }: { run: Run; trip: RunTrip }) {
  const { t } = useT();
  const nav = useNavigate();
  const pending = usePending();
  const ok = trip.visits.filter((v) => v.arrived_at && parseNaive(v.arrived_at) <= sameDayAt(run.clock.service_date, v.window[1])).length;
  return (
    <>
      <div className="apphead">
        <div />
        <ConnPill />
      </div>
      <div className="drv-pad">
        <div className="hero-ok">
          <Icon name="check" />
        </div>
        <div className="greet">{t("runDone")}</div>
        <p className="sub">{t("runDoneSub", { n: trip.visits.length, ok, sync: pending ? t("someOnPhone") : t("allSynced") })}</p>
        <News run={run} />
        <div className="card">
          <StopList trip={trip} curIdx={-1} />
        </div>
        <button className="btn block" onClick={() => nav("/driver/sync")}>
          <Icon name="sync" />
          {t("seeSync")}
        </button>
      </div>
    </>
  );
}

/** A clearly-labelled stand-in for a camera photo (laptops in a demo have no rear camera). */
function samplePhoto(label: string): string {
  const c = document.createElement("canvas");
  c.width = 960;
  c.height = 540;
  const g = c.getContext("2d")!;
  const grd = g.createLinearGradient(0, 0, 0, 540);
  grd.addColorStop(0, "#56627D");
  grd.addColorStop(1, "#2A3247");
  g.fillStyle = grd;
  g.fillRect(0, 0, 960, 540);
  g.fillStyle = "#8C7350";
  g.fillRect(0, 470, 960, 70);
  const box = (x: number, y: number, w: number, h: number, col: string) => {
    g.fillStyle = col;
    g.fillRect(x, y, w, h);
    g.fillStyle = "rgba(0,0,0,.14)";
    g.fillRect(x, y + h * 0.42, w, 10);
    g.fillStyle = "rgba(255,255,255,.75)";
    g.fillRect(x + 24, y + 24, w * 0.4, 18);
  };
  box(200, 250, 260, 220, "#C9975B");
  box(470, 300, 300, 170, "#D4A56C");
  box(290, 60, 260, 190, "#B98B5B");
  box(780, 340, 150, 130, "#E5E9F2");
  g.fillStyle = "rgba(11,13,18,.72)";
  g.fillRect(24, 24, 520, 56);
  g.fillStyle = "#fff";
  g.font = "600 26px Archivo, Arial";
  g.fillText(`Sample photo · ${label}`, 44, 62);
  return c.toDataURL("image/jpeg", 0.7);
}

/* ------------------------------------------------------------------ Report tab */

const REPORTS: [string, string, boolean][] = [
  ["traffic", "ban", true],
  ["vehicle", "wrench", true],
  ["store_closed", "door", false],
  ["refused", "hand", false],
  ["temperature", "thermo", true],
  ["late", "clock", false],
];

function ReportTab({ data }: { data: RunData }) {
  const { t } = useT();
  const toast = useToast();
  const { online } = useConnectivity();
  const items = useOutbox();
  const [sel, setSel] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [photo, setPhoto] = useState<string | null>(null);
  const run = data.run;
  const trip = run?.trips.find((x) => x.status !== "done") ?? run?.trips[run.trips.length - 1];
  const visit = trip?.visits.find((v) => v.status === "arrived") ?? trip?.visits.find((v) => v.status === "pending");
  const sent = items.filter((i) => i.type === "report");
  const send = async () => {
    if (!sel || !trip) return;
    await record("report", { trip_id: trip.id, stop_id: visit?.stop_ids[0] ?? null, kind: sel, note: note.trim(), photo }, t(`k_${sel}` as "k_late"));
    toast(online ? t("reportSent") : t("reportSaved"), online ? "green" : "", online ? "check" : "wifiOff");
    setSel(null);
    setNote("");
    setPhoto(null);
  };
  return (
    <>
      <div className="apphead">
        <div>
          <h1>{t("reportT")}</h1>
          <p className="sub">{t("reportSub")}</p>
        </div>
      </div>
      <div className="drv-pad">
        <div className="tiles">
          {REPORTS.map(([k, ic, urgent]) => (
            <button
              key={k}
              className={`tile ${sel === k ? "on" : ""} ${urgent ? "urgent" : ""}`}
              onClick={() => setSel(sel === k ? null : k)}
              aria-pressed={sel === k}
            >
              <Icon name={ic} />
              <b>{t(`k_${k}` as "k_late")}</b>
            </button>
          ))}
        </div>
        <div>
          <label className="flabel" htmlFor="rnote">
            {t("addNote")}
          </label>
          <textarea id="rnote" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("notePh")} />
        </div>
        <PhotoInput value={photo} onChange={setPhoto} label={t("addPhoto")} />
        <button className={`btn ${sel ? "primary" : ""} block bigbtn`} disabled={!sel || !trip} onClick={() => void send()}>
          <Icon name="send" />
          {online ? t("sendRep") : t("saveRep")}
        </button>
        {sent.length ? (
          <div className="card soft">
            <div className="eyebrow">{t("sentToday")}</div>
            {sent.map((r) => (
              <div key={r.id} className="listrow">
                <Icon name="flag" />
                <div>
                  <b>{r.label}</b>
                  <small>
                    {hm(r.at)}
                    {r.payload.note ? ` · ${String(r.payload.note)}` : ""}
                  </small>
                </div>
                <span className={`chip ${r.status === "synced" ? "green" : r.status === "rejected" ? "ember" : "amber"}`}>
                  {r.status === "synced" ? t("deliveredChip") : r.status === "rejected" ? t("rejected") : t("onPhone")}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ Sync tab */

function SyncTab({ data }: { data: RunData }) {
  const { t } = useT();
  const { online } = useConnectivity();
  const items = useOutbox();
  const pending = items.filter((i) => i.status === "pending");
  const lastSynced = items.filter((i) => i.syncedVirtual).sort((a, b) => (b.syncedAt ?? 0) - (a.syncedAt ?? 0))[0];
  const [changes, setChanges] = useState(() => readLS<{ text: string; kind: string; at: string }[]>(CHANGES_KEY, []));
  useEffect(() => {
    const h = () => setChanges(readLS(CHANGES_KEY, []));
    window.addEventListener("relay:changes", h);
    return () => window.removeEventListener("relay:changes", h);
  }, []);
  const moveNotices = (data.run?.notices ?? []).filter((n) => (n.key ?? "").startsWith("move:"));
  const icon = (type: string) => (type.startsWith("stop.") ? "box" : type === "report" ? "flag" : type === "run.start" ? "truck" : "check");
  return (
    <>
      <div className="apphead">
        <div>
          <h1>{t("syncT")}</h1>
          <p className="sub">{t("syncSub")}</p>
        </div>
      </div>
      <div className="drv-pad">
        <div className={`card ${online ? "" : "ember"}`}>
          <div className="conn-card">
            <Icon name={online ? "wifi" : "wifiOff"} />
            <div>
              <h3>{online ? t("connected") : t("noSignal")}</h3>
              <div className="muted sm">
                {pending.length ? t("waitingN", { n: pending.length }) : lastSynced ? t("lastSync", { t: hm(lastSynced.syncedVirtual) }) : t("nothingYet")}
              </div>
            </div>
            {online && !pending.length ? (
              <span className="chip green">
                <Icon name="check" />
                {t("upToDate")}
              </span>
            ) : online ? (
              <button className="chip blue" onClick={() => void flush()}>
                <Icon name="sync" />
                {t("syncNow")}
              </button>
            ) : null}
          </div>
        </div>
        {[...changes.slice(0, 3), ...moveNotices.slice(0, 2).map((n) => ({ text: `${n.title}. ${n.body}`, kind: n.kind, at: n.at }))].map((c, i) => (
          <div key={i} className={`card ${c.kind === "ember" ? "ember" : "blue"}`}>
            <div className="eyebrow">
              {t("changesT")} · {hm(c.at)}
            </div>
            <p style={{ marginTop: 6, fontSize: 14.5 }}>{c.text}</p>
          </div>
        ))}
        <div>
          <div className="spread" style={{ marginBottom: 8 }}>
            <div className="eyebrow">{t("records")}</div>
            {items.some((i) => i.status !== "pending") ? (
              <button className="chip outline" onClick={() => void clearSynced()}>
                {t("clearSynced")}
              </button>
            ) : null}
          </div>
          {items.length === 0 ? <p className="muted">{t("noRecords")}</p> : null}
          {items.map((x) => (
            <div key={x.id} className="qitem">
              <Icon name={icon(x.type)} />
              <div>
                <b>{x.label}</b>
                <small>
                  {t("recorded", { t: hm(x.at) })}
                  {x.offline ? ` · ${t("noSignal").toLowerCase()}` : ""}
                  {x.status === "synced" && x.syncedVirtual ? ` · ${t("syncedAt", { t: hm(x.syncedVirtual) })}` : ""}
                </small>
                {x.status === "rejected" && x.message ? <small className="err">{x.message}</small> : null}
              </div>
              <span className={`chip ${x.status === "synced" ? "green" : x.status === "rejected" ? "ember" : "amber"}`}>
                <Icon name={x.status === "synced" ? "check" : x.status === "rejected" ? "x" : "clock"} />
                {x.status === "synced" ? t("synced") : x.status === "rejected" ? t("rejected") : t("waiting")}
              </span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ Me tab */

function MeTab({ data, dark, setDark, setLang }: { data: RunData; dark: boolean; setDark: (v: boolean) => void; setLang: (l: Lang) => void }) {
  const { t, lang } = useT();
  const me = useMe().data!;
  const { simulated } = useConnectivity();
  const clock = useClockState();
  const run = data.run;
  const visits = run?.trips.flatMap((x) => x.visits) ?? [];
  const done = visits.filter((v) => DONE.includes(v.status));
  const onTime = done.filter((v) => v.arrived_at && run && parseNaive(v.arrived_at) <= sameDayAt(run.clock.service_date, v.window[1])).length;
  const cases = done.reduce((a, v) => a + v.orders.reduce((b, o) => b + o.units, 0), 0);
  const v = run?.vehicle;
  return (
    <div className="drv-pad" style={{ paddingTop: 22 }}>
      <div className="me-head">
        <span className="av" style={{ background: me.color }}>
          {me.initials}
        </span>
        <div>
          <h1>{me.name}</h1>
          <p className="sub">{t("driverAt", { depot: run?.depot_name ?? me.depot ?? "" })}</p>
        </div>
      </div>
      <div className="stats">
        <div>
          <b>
            {done.length}/{visits.length}
          </b>
          <small>{t("stopsToday")}</small>
        </div>
        <div>
          <b>{done.length ? `${Math.round((onTime / done.length) * 100)}%` : "–"}</b>
          <small>{t("onTimeToday")}</small>
        </div>
        <div>
          <b>{cases}</b>
          <small>{t("casesToday")}</small>
        </div>
      </div>
      <div className="card">
        <div className="setrow">
          <Icon name={v?.type === "van" ? "van" : "truck"} />
          <div>
            <b>{v?.id ?? me.vehicle_id}</b>
            <small>{v ? `${vehicleName(t, v.type, v.temp)} · ${f1(v.m3)} m³ · ${kg(v.kg)} kg` : ""}</small>
          </div>
        </div>
        <div className="setrow">
          <Icon name="user" />
          <div>
            <b>{t("dispatch")}</b>
            <small>
              {run?.dispatcher ?? ""} · {me.workspace.sandbox ? `sandbox ${me.workspace.code}` : "Waypoint"}
            </small>
          </div>
        </div>
      </div>
      <div className="card">
        <div className="setrow">
          <Icon name="globe" />
          <div>
            <b>{t("language")}</b>
          </div>
          <div className="seg" role="group" aria-label={t("language")}>
            {LANGS.map(([k, label]) => (
              <button key={k} className={lang === k ? "on" : ""} onClick={() => setLang(k)} lang={k} aria-pressed={lang === k}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="setrow">
          <Icon name="moon" />
          <div>
            <b>{t("night")}</b>
            <small>{t("nightSub")}</small>
          </div>
          <button className={`toggle ${dark ? "on" : ""}`} role="switch" aria-checked={dark} aria-label={t("night")} onClick={() => setDark(!dark)} />
        </div>
        <div className="setrow">
          <Icon name="wifiOff" />
          <div>
            <b>{t("offlineTest")}</b>
            <small>{t("offlineTestSub")}</small>
          </div>
          <button
            className={`toggle ${simulated ? "on" : ""}`}
            role="switch"
            aria-checked={simulated}
            aria-label={t("offlineTest")}
            onClick={() => setSimulatedOffline(!simulated)}
          />
        </div>
      </div>
      <button className="btn block lg" onClick={signOut}>
        <Icon name="logout" />
        {t("signOut")}
      </button>
      <p className="fine">
        Relay · {me.workspace.code} · {hm(clock.payload?.now ?? virtualNow())}
      </p>
    </div>
  );
}
