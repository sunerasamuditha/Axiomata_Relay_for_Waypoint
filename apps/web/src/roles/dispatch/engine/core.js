/**
 * Dispatcher engine: state and helpers.
 *
 * This is the approved dispatcher prototype (vanilla JS, string templates) running on live data.
 * `S` mirrors the server snapshot (GET /api/dispatch/snapshot) plus local view state. Times are
 * minutes after midnight of the service date (negative the evening before), exactly as the
 * prototype used them, so its rendering code is unchanged in spirit.
 */
import { ICONS } from "../../../ui/icons";
import { parseNaive, virtualNow } from "../../../lib/clock";

export const ic = (n, c = "") => `<svg class="ic ${c}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ""}</svg>`;
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const fmt = (m) => {
  if (m == null || !Number.isFinite(m)) return "--:--";
  m = Math.round(m);
  const d = ((m % 1440) + 1440) % 1440;
  return String(Math.floor(d / 60)).padStart(2, "0") + ":" + String(d % 60).padStart(2, "0");
};
export const hm = (s) => {
  const [h, m] = s.split(":").map(Number);
  return h * 60 + m;
};
export const dur = (m) => {
  m = Math.max(0, Math.round(m));
  const h = Math.floor(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m} min`;
};
export const f1 = (n) => (Math.round(n * 10) / 10).toFixed(1);
export const kg = (n) => Math.round(n).toLocaleString("en-US");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const dayLabel = (iso) => {
  const d = new Date(parseNaive(iso));
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
};
export const weekdayLong = (iso) => ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][new Date(parseNaive(iso)).getUTCDay()];

/* ------------------------------------------------------------------ state */

export const DEPOTS = {};
export const DEPOT_CODE = {};
export const DISTRICTS = {};
export const SERVICE = {};

export const S = {
  theme: "dark",
  demoOpen: false,
  dview: "canvas",
  dfilter: "all",
  dsel: null,
  manualPos: {},
  leverPending: null,
  lever: null, // GET /plans/{id}/lever result
  leverFor: null,
  leverError: null,
  outlook: null,
  outletsReady: false,
  outlets: {},
  vehicles: {},
  trips: {},
  orders: {},
  feed: [],
  issues: [],
  pendingMoves: [],
  plan: null,
  phase: "ordering",
  queue: null,
  clock: 0,
  clockPayload: null,
  serviceDate: null,
  base: 0,
  me: null,
  version: 0,
};

/** Hooks the entry module fills in (avoids import cycles). */
export const hooks = { refresh: () => {}, act: () => {}, api: null };

export function hydrateRef(ref) {
  for (const d of ref.depots) {
    DEPOT_CODE[d.id] = d.code;
    DEPOTS[d.code] = { id: d.code, name: d.name, short: d.id, kind: d.kind };
  }
  for (const d of ref.districts) DISTRICTS[d.name] = { depot: DEPOT_CODE[d.depot], out: d.out, inter: d.inter, km: d.km, ikm: d.ikm, road: d.road };
  for (const a of ref.allowance) (SERVICE[a.brand] ||= {})[a.dock] = a.minutes;
  for (const o of ref.outlets) {
    S.outlets[o.id] = {
      id: o.id,
      name: o.name,
      brand: o.brand,
      district: o.district,
      dock: o.dock,
      park: o.parking === "van_only" ? "van" : o.parking === "mall_dock" ? "mall" : "normal",
      mallWindow: o.mall_window,
      open: hm(o.open),
      close: hm(o.close),
      depot: DEPOT_CODE[o.depot],
      manager: o.manager,
    };
  }
  S.outletsReady = true;
}

/** Minutes after midnight of the service date for a naive ISO time (null-safe). */
export const M = (iso) => (iso ? (parseNaive(iso) - S.base) / 60000 : null);

export function hydrate(snap) {
  S.serviceDate = snap.service_date;
  S.base = parseNaive(snap.service_date);
  S.clockPayload = snap.clock;
  S.clock = M(snap.clock.now);
  S.phase = snap.phase;
  const prevPlan = S.plan;
  S.plan = snap.plan;
  S.queue = snap.queue;
  S.issues = snap.issues;
  S.pendingMoves = snap.pending_moves;
  const code = (d) => DEPOT_CODE[d] || d;

  S.vehicles = {};
  for (const v of snap.vehicles) {
    S.vehicles[v.id] = {
      id: v.id,
      type: v.type,
      temp: v.temp,
      kg: v.kg,
      m3: v.m3,
      kmpl: v.kmpl,
      quota: v.quota,
      depot: code(v.depot),
      status: v.status,
      driver: v.driver,
      used: v.fuel_used || 0,
      backOn: v.back_on,
    };
  }
  const codeById = {};
  S.trips = {};
  for (const t of snap.trips) {
    codeById[t.id] = t.code;
    S.trips[t.code] = {
      id: t.code,
      dbId: t.id,
      veh: t.vehicle,
      n: t.n,
      brand: t.brand,
      district: t.district,
      depot: code(t.depot),
      status: t.status,
      depart: M(t.depart),
      ret: M(t.return),
      minutes: t.minutes,
      km: t.km,
      litres: t.litres,
      m3: t.m3,
      kg: t.kg,
      locked: t.locked,
      controlled: t.controlled === "human",
      signal: t.signal,
      darkSince: M(t.dark_since),
      lastContact: M(t.last_contact),
      releasedAt: M(t.released_at),
      departedAt: M(t.departed_at),
      completedAt: M(t.completed_at),
      seal: t.seal,
      tempC: t.temp_c,
      dock: t.dock,
      bay: t.bay,
      driver: t.driver || S.vehicles[t.vehicle]?.driver || "",
      stops: t.stops.map(String),
      loading: t.loading,
    };
  }
  S.orders = {};
  for (const o of snap.orders) {
    const st = o.status;
    const delivered = ["delivered", "partial", "failed", "received"].includes(st);
    S.orders[String(o.id)] = {
      id: String(o.id),
      dbId: o.id,
      ref: o.ref,
      outlet: o.outlet,
      brand: o.brand,
      district: o.district,
      depot: code(o.depot),
      temp: o.temp,
      units: o.units,
      m3: o.m3,
      kg: o.kg,
      status: st,
      trip: o.trip ? codeById[o.trip] : null,
      seq: o.seq,
      eta: M(o.eta) ?? M(o.planned_arrival),
      etaKind: o.eta_kind,
      bandLo: M(o.band_lo) ?? (M(o.eta) != null ? M(o.eta) - 4 : null),
      bandHi: M(o.band_hi) ?? (M(o.eta) != null ? M(o.eta) + 8 : null),
      planned: M(o.planned_arrival),
      latePlanned: !!o.late_planned,
      risk: o.risk ?? 0,
      svc: o.svc,
      arrivedAt: M(o.arrived_at),
      delivered,
      deliveredAt: M(o.arrived_at) ?? M(o.delivered_at),
      completedAt: M(o.delivered_at),
      outcome: st === "partial" ? "partial" : st === "failed" ? "failed" : "delivered",
      received: !!o.received,
      receipt: o.receipt,
      deferred: o.deferred
        ? { kind: o.deferred.kind === "unavoidable" ? "unavoidable" : "choice", code: o.deferred.code, text: o.deferred.text, streak: o.deferred.streak || 1, cost: o.deferred.cost || {} }
        : null,
      deferredYesterday: o.deferred_yesterday,
      daysSince: o.days_since,
      placedAt: M(o.placed_at),
      channel: o.channel,
      note: o.note,
      lines: o.lines || [],
      proof: o.proof,
      pendingMove: o.pending_move,
    };
  }
  S.feed = snap.feed.map((n) => ({ id: String(n.id), kind: n.kind, icon: n.icon || "info", title: n.title, body: esc(n.body), at: fmt(M(n.at)), act: n.actions || [], read: n.read }));
  if (S.dsel && !S.dsel.startsWith("g-")) {
    const k = S.dsel.slice(2);
    if ((S.dsel[0] === "o" && !S.orders[k]) || (S.dsel[0] === "t" && !S.trips[k])) S.dsel = null;
  }
  const changed = !prevPlan || !snap.plan || prevPlan.id !== snap.plan.id || prevPlan.version !== snap.plan.version;
  S.version++;
  return { planChanged: changed };
}

/** Live clock (virtual minutes) between snapshots. */
export function nowMin() {
  return (virtualNow() - S.base) / 60000;
}

/* ------------------------------------------------------------------ derived */

export function tripOrders(t) {
  return t.stops.map((id) => S.orders[id]).filter(Boolean);
}
export function tripState(t) {
  return t.status;
}
export function isDark(t) {
  return t.signal === "dark" && t.status === "out";
}
export function orderStatus(o) {
  if (o.deferred || o.status === "deferred") return "deferred";
  const s = o.status;
  if (s === "received" || s === "partial" || s === "failed" || s === "delivered") return s;
  if (s === "arrived") return "out";
  if (["planned", "loading", "loaded", "out"].includes(s)) return s;
  const t = S.trips[o.trip];
  return t ? (t.status === "done" ? "delivered" : t.status) : "planned";
}
export const STATUS_LABEL = {
  planned: "Planned",
  loading: "Loading",
  loaded: "Loaded",
  out: "On the road",
  delivered: "Delivered",
  partial: "Delivered, partial",
  failed: "Not delivered",
  received: "Received",
  deferred: "Deferred",
  done: "Complete",
  placed: "Ordered",
  confirmed: "Confirmed",
};
export function isConflict(o) {
  return !o.deferred && !o.delivered && o.latePlanned;
}
export function vehiclePos(t) {
  const os = tripOrders(t);
  const done = os.filter((o) => o.delivered).length;
  if (isDark(t)) {
    const now = nowMin();
    const est = os.filter((o) => o.delivered || (o.eta != null && now >= o.eta)).length;
    return { idx: clamp(Math.max(est, done), 0, os.length), est: true };
  }
  return { idx: done, est: false };
}
export function freshMinutes(vehId) {
  return Object.values(S.trips)
    .filter((t) => t.veh === vehId && t.stops.length && t.brand === "Fresh")
    .reduce((s, t) => s + (t.minutes || 0), 0);
}
export function dayMinutes(vehId) {
  return Object.values(S.trips)
    .filter((t) => t.veh === vehId && t.stops.length && t.brand !== "Fresh")
    .reduce((s, t) => s + (t.minutes || 0), 0);
}
export function shortIssueFor(o) {
  return S.issues.find((i) => i.order === o.dbId && i.kind.endsWith("_at_dock"));
}

export function relaySteps(o) {
  const st = orderStatus(o);
  const t = S.trips[o.trip];
  const order = ["placed", "planned", "loaded", "out", "delivered", "received"];
  const labels = { placed: "Ordered", planned: "Planned", loaded: "Loaded", out: "On road", delivered: "Delivered", received: "Received" };
  if (st === "deferred") return order.map((k, i) => ({ k, label: i === 1 ? "Deferred" : labels[k], cls: i === 0 ? "done" : i === 1 ? "warn" : "" }));
  const [done, now] =
    { planned: [2, -1], loading: [2, 2], loaded: [3, -1], out: [3, 3], delivered: [5, 5], partial: [5, 5], failed: [4, 4], received: [6, -1], done: [5, 5] }[st] || [2, -1];
  const est = t && isDark(t) && st === "out";
  return order.map((k, i) => ({
    k,
    label: k === "delivered" && st === "partial" ? "Partial" : k === "delivered" && st === "failed" ? "Failed" : labels[k],
    cls: i < done ? (k === "delivered" && (st === "partial" || st === "failed") ? "warn" : "done") : i === now ? "now" + (est ? " est" : "") : "",
  }));
}
export function relayHTML(o) {
  return `<div class="relay">${relaySteps(o)
    .map((s) => `<div class="st ${s.cls}"><i></i>${s.label}</div>`)
    .join("")}</div>`;
}

export function windowBar(o, opts = {}) {
  const ou = S.outlets[o.outlet];
  const now = nowMin();
  const showNow = now >= ou.open - 240 && now <= ou.close + 240;
  const lo = Math.min(ou.open, o.bandLo ?? ou.open, showNow ? now : ou.open) - 25;
  const hi = Math.max(ou.close, o.bandHi ?? ou.close) + 25;
  const span = Math.max(60, hi - lo);
  const X = (m) => clamp(((m - lo) / span) * 100, 0, 100);
  const est = opts.est;
  const risk = (o.risk || 0) > 0.45;
  let h = `<div class="wbar"><div class="track"></div><div class="win" style="left:${X(ou.open)}%;width:${X(ou.close) - X(ou.open)}%"></div>`;
  if (!o.deferred && o.bandLo != null && !o.delivered) {
    h += `<div class="band ${risk ? "risk" : ""} ${est ? "est" : ""}" style="left:${X(o.bandLo)}%;width:${Math.max(2, X(o.bandHi) - X(o.bandLo))}%"></div>`;
  }
  if (o.delivered && o.deliveredAt != null) h += `<div class="act ${o.deliveredAt > ou.close ? "late" : ""}" style="left:${X(o.deliveredAt)}%"></div>`;
  if (showNow && now >= lo && now <= hi) h += `<div class="now" style="left:${X(now)}%"></div>`;
  h += `<div class="lbl" style="left:${X(ou.open)}%">${fmt(ou.open)}</div><div class="lbl" style="left:${X(ou.close)}%">${fmt(ou.close)}</div></div>`;
  return h;
}
export function etaText(o) {
  if (o.deferred) return "Deferred";
  if (o.delivered) return "Arrived " + fmt(o.deliveredAt);
  if (o.bandLo == null) return "—";
  return "~" + fmt(o.bandLo + 4) + "–" + fmt(o.bandHi);
}

/* ------------------------------------------------------------------ toasts and modals */

export function toast(msg, kind = "", icon = "info") {
  let box = document.querySelector("body > .toasts");
  if (!box) {
    box = document.createElement("div");
    box.className = "toasts";
    document.body.appendChild(box);
  }
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.innerHTML = ic(icon) + "<div>" + msg + "</div>";
  box.appendChild(t);
  setTimeout(() => {
    t.style.transition = "opacity .3s";
    t.style.opacity = "0";
    setTimeout(() => t.remove(), 300);
  }, 4200);
}

export function dk() {
  return S.theme === "light" ? "" : "dark-ui";
}
export function openModal(html) {
  closeModal();
  const m = document.createElement("div");
  m.className = "modal-back";
  m.id = "modal";
  m.innerHTML = `<div class="modal ${dk()}" role="dialog" aria-modal="true">${html}</div>`;
  m.addEventListener("click", (e) => {
    if (e.target === m) closeModal();
  });
  document.body.appendChild(m);
}
export function closeModal() {
  const m = document.getElementById("modal");
  if (m) m.remove();
}
export function setBusy(title, sub) {
  let b = document.getElementById("busy");
  if (!title) {
    if (b) b.remove();
    return;
  }
  if (!b) {
    b = document.createElement("div");
    b.id = "busy";
    b.className = "busy";
    document.body.appendChild(b);
  }
  b.innerHTML = `<div class="card"><b>${title}</b><small>${sub || ""}</small><div class="bar"><i></i></div></div>`;
}
