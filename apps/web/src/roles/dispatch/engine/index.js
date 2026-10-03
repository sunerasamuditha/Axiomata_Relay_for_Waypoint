/**
 * Dispatcher engine entry: mounts the desk into a host element and wires every action to the API.
 *
 *   const eng = mountDispatcher(host, { reference, snapshot, me, api, signOut, refetch })
 *   eng.update(snapshot)   // after every refetch (live updates arrive as SSE invalidations)
 *   eng.destroy()
 */
import { $, closeModal, dayLabel, dk, esc, fmt, hooks, hydrate, hydrateRef, ic, isDark, M, nowMin, openModal, S, setBusy, toast } from "./core";
import {
  centerOn,
  closeAll,
  CV,
  cvSync,
  drawMinimap,
  fitView,
  mountCanvas,
  openDeferModal,
  openMoveModal,
  openShortfallModal,
  relayout,
  renderInspector,
  selectNode,
  zoomBy,
} from "./canvas";
import { bindLever, LEVER_LABEL, leverPageHTML, ordersPageHTML, outlookPageHTML, placeKnob } from "./pages";

let ROOT = null;
let CTX = null;
let CLEAN = [];
let TICK = null;
let LASTPAGE = null;
let LEVER_REQ = null;

export function mountDispatcher(root, ctx) {
  ROOT = root;
  CTX = ctx;
  hooks.api = ctx.api;
  hooks.act = doAct;
  hooks.refresh = refresh;
  hydrateRef(ctx.reference);
  S.me = ctx.me;
  let th = "dark";
  try {
    th = localStorage.getItem("wp-dispatch-theme") || "dark";
  } catch {
    /* ignore */
  }
  const q = new URLSearchParams(location.search);
  if (q.get("theme")) th = q.get("theme");
  S.theme = th;
  document.documentElement.dataset.theme = th;
  S.demoOpen = q.get("demo") === "1";
  hydrate(ctx.snapshot);
  render();

  const onClick = (e) => {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    if (!ROOT.contains(el) && !el.closest("#modal,#demo")) return;
    e.preventDefault();
    doAct(el.dataset.act, el);
  };
  const onKey = (e) => {
    const tag = e.target && e.target.tagName;
    if (e.target && e.target.id === "dsearch" && e.key === "Enter") return search(e.target.value);
    if (e.key === "Escape") {
      if (document.getElementById("modal")) return closeModal();
      if (S.dsel) {
        S.dsel = null;
        cvSync();
      }
      return;
    }
    if (e.key === "." && !/INPUT|TEXTAREA/.test(tag)) {
      S.demoOpen = !S.demoOpen;
      renderDemo();
    }
    if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("[role=button][data-act]")) {
      e.preventDefault();
      doAct(e.target.dataset.act);
    }
  };
  const onResize = () => {
    if (CV.el) drawMinimap();
  };
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", onResize);
  CLEAN.push(() => document.removeEventListener("click", onClick));
  CLEAN.push(() => document.removeEventListener("keydown", onKey));
  CLEAN.push(() => window.removeEventListener("resize", onResize));
  CLEAN.push(bindLever(updateDispatcher));
  TICK = setInterval(tick, 1000);
  schedulePrefetch();
  return { update, destroy };
}

function destroy() {
  clearInterval(TICK);
  clearTimeout(PREFETCH);
  CLEAN.forEach((f) => f());
  CLEAN = [];
  closeAll();
  setBusy(null);
  document.getElementById("demo")?.remove();
  document.querySelector("body > .toasts")?.remove();
  if (ROOT) ROOT.innerHTML = "";
  CV.el = null;
  ROOT = null;
}

function update(snap) {
  const prevKey = S.plan ? `${S.plan.id}` : "";
  const { planChanged } = hydrate(snap);
  const newPlan = (S.plan ? `${S.plan.id}` : "") !== prevKey;
  if (newPlan) {
    Object.keys(S.manualPos).forEach((k) => k[0] !== "g" && delete S.manualPos[k]);
    S.dsel = null;
    S.leverPending = null;
  }
  refresh();
  if (CV.el && planChanged) {
    if (newPlan) {
      relayout(false);
      requestAnimationFrame(() => fitView(true));
    } else relayout(true);
  }
  schedulePrefetch();
}

let PREFETCH = null;
/** A fresh draft is when the dispatcher usually opens the lever next: solve the alternatives early,
 *  but only after a pause, so the request never competes with what she is doing right now. */
function schedulePrefetch() {
  clearTimeout(PREFETCH);
  if (!S.plan || S.plan.status !== "draft") return;
  PREFETCH = setTimeout(() => {
    if (S.plan && S.plan.status === "draft" && !document.getElementById("busy")) prefetchLever();
  }, 1500);
}

/* ------------------------------------------------------------------ shell */

function render() {
  ROOT.innerHTML = `<div class="desk ${dk()}">
    <nav class="rail" id="drail"></nav>
    <div class="dmain" id="dmain">
      <div class="dview" id="dcanvas"></div>
      <div class="dview" id="dpage" hidden></div>
      <header class="cmdbar" id="cmdbar"></header>
    </div>
  </div>`;
  mountCanvas($("#dcanvas", ROOT));
  updateDispatcher();
  renderDemo();
}
export function refresh() {
  updateDispatcher();
  renderDemo();
}

function railHTML() {
  const defN = Object.values(S.orders).filter((o) => o.deferred).length;
  const queueN = S.queue && S.queue.date !== S.serviceDate ? S.queue.orders.length : 0;
  const it = [
    ["canvas", "target", "Canvas"],
    ["lever", "lever", "Deferrals", defN],
    ["orders", "list", "Orders", queueN || ""],
    ["outlook", "chart", "Outlook"],
  ];
  const lt = S.theme === "light";
  const me = S.me || { initials: "NP", name: "Dispatcher" };
  return (
    `<div class="logo" title="Waypoint"><img src="/logo.png" alt="Waypoint"></div>` +
    it.map(([k, i, l, b]) => `<button class="${S.dview === k ? "on" : ""}" data-act="dview:${k}" aria-label="${l}">${ic(i)}${l}${b ? `<span class="badge">${b}</span>` : ""}</button>`).join("") +
    `<div class="rail-foot"><button data-act="theme" aria-label="Switch to ${lt ? "dark" : "light"} theme">${ic(lt ? "moon" : "sun")}${lt ? "Dark" : "Light"}</button><button class="${S.demoOpen ? "on" : ""}" data-act="demo" aria-label="Presenter controls">${ic("play")}Demo</button><button class="me" data-act="me" aria-label="${esc(me.name)}"><span class="avatar av-d" style="width:34px;height:34px;font-size:12px;background:${esc(me.color || "#1F4BFF")}">${esc(me.initials)}</span></button></div>`
  );
}

function cmdbarHTML() {
  const trips = Object.values(S.trips).filter((t) => t.stops.length);
  const loaded = trips.filter((t) => ["loaded", "out", "done"].includes(t.status)).length;
  const road = trips.filter((t) => t.status === "out").length;
  const dark = trips.filter(isDark).length;
  const served = Object.values(S.orders).filter((o) => !o.deferred && o.trip);
  const del = served.filter((o) => o.delivered).length;
  const defN = Object.values(S.orders).filter((o) => o.deferred).length;
  const cp = S.clockPayload || {};
  const closedAt = cp.orders_closed_at ? fmt(M(cp.orders_closed_at)) : null;
  const plan = S.plan;
  const started = trips.some((t) => t.status !== "planned");
  let primary = "";
  if (!plan) primary = `<button class="dbtn primary" data-act="plan">${ic("layers")}${S.phase === "closed" ? "Plan now" : "Close orders and plan"}</button>`;
  else if (plan.status === "draft") primary = `<button class="dbtn primary" data-act="publish">${ic("send")}Publish v${plan.version}</button>`;
  else primary = `<button class="dbtn primary" data-act="replan" ${started ? 'disabled title="Loading has started: move or defer orders one at a time instead"' : ""}>${ic("sync")}Re-plan</button>`;
  const clockLine = `<span class="tclock" data-act="demo" role="button" tabindex="0" title="Demo clock">${S.clockPayload?.paused ? "" : '<span class="live run"></span>'}<b id="cmdclock">${fmt(nowMin())}</b> ${S.clockPayload?.paused ? "paused" : `${S.clockPayload?.rate ?? 1}×`}</span>`;
  return `<div class="ttl">Dispatch<small>Waypoint · ${S.serviceDate ? dayLabel(S.serviceDate) : ""} · ${clockLine}</small></div>
  <div class="pipe">${pipeHTML({ plan, closedAt, trips, loaded, road, dark, del, served })}</div>
  <label class="dsearch">${ic("search")}<input id="dsearch" placeholder="Find outlet, order or vehicle" autocomplete="off"></label>
  <button class="dbtn" data-act="dview:lever">${ic("lever")}Deferrals <span class="cnt">${defN}</span></button>
  ${primary}`;
}

function pipeHTML({ plan, closedAt, trips, loaded, road, dark, del, served }) {
  const st = [];
  if (!plan) {
    st.push(`<div class="ps ${S.phase === "ordering" ? "go" : "ok"}">${ic(S.phase === "ordering" ? "clock" : "check")}${S.phase === "ordering" ? "Orders open · cutoff" : "Orders closed"} <b>${S.phase === "ordering" ? "16:00" : closedAt || ""}</b></div>`);
    st.push(`<div class="ps">${ic("layers")}No plan yet</div>`);
  } else if (plan.status === "draft") {
    st.push(`<div class="ps ok">${ic("check")}Orders closed <b>${closedAt || ""}</b></div>`);
    st.push(`<div class="ps draft">${ic("layers")}Plan v${plan.version} draft <b>${fmt(M(plan.created_at))}</b></div>`);
    st.push(`<div class="ps">${ic("lever")}${LEVER_LABEL[plan.policy]?.label || plan.policy}</div>`);
  } else {
    st.push(`<div class="ps ok">${ic("layers")}Plan v${plan.version} <b>${fmt(M(plan.published_at || plan.created_at))}</b></div>`);
    st.push(`<div class="ps go">${ic("box")}Loaded <b>${loaded}/${trips.length}</b></div>`);
    st.push(`<div class="ps go">${ic("truck")}On road <b>${road}</b></div>${dark ? `<div class="ps warn">${ic("wifiOff")}No signal <b>${dark}</b></div>` : ""}`);
    st.push(`<div class="ps ok">${ic("check")}Delivered <b>${del}/${served.length}</b></div>`);
  }
  return st.join(`<span class="sep">${ic("chevR")}</span>`);
}

function updateDispatcher() {
  if (!ROOT || !$("#drail", ROOT)) return;
  $("#drail", ROOT).innerHTML = railHTML();
  const search = $("#dsearch", ROOT);
  const keep = search && document.activeElement === search ? search.value : null;
  $("#cmdbar", ROOT).innerHTML = cmdbarHTML();
  if (keep !== null) {
    const s2 = $("#dsearch", ROOT);
    s2.value = keep;
    s2.focus();
  }
  const onCanvas = S.dview === "canvas";
  $("#dcanvas", ROOT).hidden = !onCanvas;
  const pg = $("#dpage", ROOT);
  pg.hidden = onCanvas;
  if (!onCanvas) {
    if (S.dview === "lever") ensureLever();
    if (S.dview === "outlook") ensureOutlook();
    const html = S.dview === "lever" ? leverPageHTML() : S.dview === "orders" ? ordersPageHTML() : outlookPageHTML();
    const sc = pg.firstElementChild && LASTPAGE === S.dview ? pg.firstElementChild.scrollTop : 0;
    LASTPAGE = S.dview;
    pg.innerHTML = `<div class="dpage">${html}</div>`;
    pg.firstElementChild.scrollTop = sc;
    if (S.dview === "lever") placeKnob();
  } else {
    cvSync();
    if (CV.needsFit)
      requestAnimationFrame(() => {
        if (CV.el && CV.el.getBoundingClientRect().width) {
          CV.needsFit = false;
          fitView(false);
        }
      });
  }
}

function tick() {
  const c = $("#cmdclock", ROOT || document);
  if (c) c.textContent = fmt(nowMin());
  const d = document.getElementById("democlock");
  if (d) d.textContent = fmt(nowMin());
}

/* ------------------------------------------------------------------ data the pages need */

function leverKey() {
  return S.plan ? `${S.plan.id}:${S.plan.version}` : null;
}
function prefetchLever() {
  if (S.plan && S.leverFor !== leverKey() && !LEVER_REQ) ensureLever();
}
function ensureLever() {
  const key = leverKey();
  if (!key || S.leverFor === key || LEVER_REQ === key) return;
  LEVER_REQ = key;
  S.leverError = null;
  hooks.api
    .get(`/api/dispatch/plans/${S.plan.id}/lever`)
    .then((data) => {
      if (leverKey() !== key) return;
      S.lever = data;
      S.leverFor = key;
      if (!S.leverPending) S.leverPending = S.plan.policy;
    })
    .catch((e) => {
      S.leverError = String(e.message || e);
    })
    .finally(() => {
      LEVER_REQ = null;
      if (S.dview === "lever") updateDispatcher();
    });
}
function ensureOutlook() {
  if (S.outlook) return;
  S.outlook = null;
  hooks.api
    .get("/api/dispatch/outlook")
    .then((d) => (S.outlook = d))
    .catch((e) => (S.outlook = { error: String(e.message || e) }))
    .finally(() => S.dview === "outlook" && updateDispatcher());
}

/* ------------------------------------------------------------------ presenter controls */

function step(done, title, hint) {
  return { done, title, hint };
}
function walkthrough() {
  const v57 = Object.values(S.trips).find((t) => t.veh === "VEH057");
  const fathima = Object.values(S.orders).filter((o) => o.outlet === "OUT105");
  return [
    step(!!S.plan, "Close orders and plan", "On the canvas, after the 16:00 cutoff. The optimiser plans both depots in a few seconds."),
    step(S.plan?.policy === "fairness", "Pull the lever to Fairness first", "Deferrals page. See who comes back and what it costs before you publish."),
    step(S.plan?.status === "published", "Publish the plan", "Docks, drivers and stores hear it at once."),
    step(!!v57 && v57.status !== "planned", "Dock: load VEH057 at Kandy", "Another browser: <code>kasun@waypoint.lk</code> / <code>relay2026</code>. Tick lines, flag a shortfall."),
    step(S.issues.some((i) => i.kind.endsWith("_at_dock") && i.status !== "open"), "Decide the shortfall", "It lands in your feed. Partial, hold, or re-order for tomorrow."),
    step(!!v57 && ["out", "done"].includes(v57.status), "Driver: release, then start the run", "A phone: <code>sunil@waypoint.lk</code>. Release on the dock first."),
    step(!!v57 && (v57.signal === "dark" || S.pendingMoves.length > 0), "Dark Corridor", "Driver: Me → Test offline mode. VEH057 goes dark here within a minute."),
    step(S.pendingMoves.some((p) => p.status !== "queued"), "Move a stop while dark, then reconnect", "Queue a move from the trip inspector; deliver offline on the phone; reconnect. The delivery wins."),
    step(fathima.some((o) => o.received), "Store: confirm receipt", "<code>fathima@waypoint.lk</code>. Report a damaged case; it comes back to you."),
  ];
}
function renderDemo() {
  let el = document.getElementById("demo");
  if (!el) {
    el = document.createElement("aside");
    el.id = "demo";
    el.className = "demo";
    document.body.appendChild(el);
  }
  el.hidden = !S.demoOpen;
  if (!S.demoOpen) return;
  const cp = S.clockPayload || {};
  const steps = walkthrough();
  const next = steps.findIndex((s) => !s.done);
  const rate = cp.paused ? 0 : cp.rate;
  el.innerHTML = `<header><div><b>Presenter controls</b><small>The shared demo clock and reset. The dock, the driver and the store are their own apps: sign in to them in other tabs or phones.</small></div><button class="x" data-act="demo" aria-label="Close presenter controls">${ic("x")}</button></header>
  <div class="dclock"><div><b class="num" id="democlock">${fmt(nowMin())}</b><small>${S.serviceDate ? (nowMin() < 0 ? `the evening before ${dayLabel(S.serviceDate)}` : dayLabel(S.serviceDate)) : ""}</small></div><button class="dbtn" data-act="${cp.paused ? "clock:run:1" : "clock:pause"}">${ic(cp.paused ? "play" : "pause")}${cp.paused ? "Run" : "Pause"}</button><button class="dbtn" data-act="clock:add:15">+15 min</button><button class="dbtn" data-act="clock:add:60">+1 h</button></div>
  <div class="rates">${[1, 10, 60].map((r) => `<button class="dbtn ${rate === r ? "on" : ""}" data-act="clock:run:${r}">${r}×</button>`).join("")}</div>
  <ol class="walk">${steps.map((s, i) => `<li class="${s.done ? "done" : i === next ? "now" : ""}"><span class="n">${s.done ? ic("check") : i + 1}</span><div><b>${s.title}</b><small>${s.hint}</small></div></li>`).join("")}</ol>
  <div class="wsrow"><span>Workspace <span class="mono">${esc(cp.workspace_code || "MAIN")}</span>${cp.sandbox ? " · private sandbox" : " · shared"}</span><button class="dbtn" data-act="reset">${ic("reset")}Reset demo</button></div>`;
}

/* ------------------------------------------------------------------ actions */

async function call(fn, okMsg, kind = "green", icon = "check") {
  try {
    const out = await fn();
    if (okMsg) toast(typeof okMsg === "function" ? okMsg(out) : okMsg, kind, icon);
    await CTX.refetch();
    return out;
  } catch (e) {
    toast(esc(String(e.message || e)), "ember", "alert");
    return null;
  } finally {
    setBusy(null);
  }
}

function search(q) {
  q = (q || "").trim().toLowerCase();
  if (!q) return;
  const o = Object.values(S.orders).find((o) => (S.outlets[o.outlet]?.name.toLowerCase().includes(q) || o.ref.toLowerCase().includes(q)) && CV.els["o-" + o.id]);
  const t = !o && Object.values(S.trips).find((t) => (t.veh.toLowerCase().includes(q) || t.id.toLowerCase() === q) && CV.els["t-" + t.id]);
  if (S.dview !== "canvas") {
    S.dview = "canvas";
    updateDispatcher();
  }
  if (o) {
    centerOn("o-" + o.id, 0.95);
    selectNode("o-" + o.id);
  } else if (t) {
    centerOn("t-" + t.id, 0.85);
    selectNode("t-" + t.id);
  } else toast(`No outlet, order or vehicle matches “${esc(q)}”.`, "", "search");
}

function setTheme(t) {
  S.theme = t;
  document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem("wp-dispatch-theme", t);
  } catch {
    /* ignore */
  }
  const desk = $(".desk", ROOT);
  if (desk) desk.classList.toggle("dark-ui", t === "dark");
  document.querySelectorAll(".modal").forEach((m) => m.classList.toggle("dark-ui", t === "dark"));
  updateDispatcher();
}

function openMe() {
  const me = S.me || {};
  const cp = S.clockPayload || {};
  openModal(`<div class="mehead"><span class="avatar" style="width:52px;height:52px;font-size:17px;background:${esc(me.color || "#1F4BFF")}">${esc(me.initials || "")}</span><div><b>${esc(me.name || "")}</b><small>${esc(me.title || "Dispatcher")}</small><small>${esc(me.email || "")} · workspace ${esc(cp.workspace_code || "MAIN")}</small></div></div>
  <div class="iacts" style="margin-top:20px"><button class="dbtn" data-act="theme">${ic(S.theme === "light" ? "moon" : "sun")}${S.theme === "light" ? "Dark theme" : "Light theme"}</button><button class="dbtn" data-act="demo">${ic("play")}Presenter controls</button><button class="dbtn ember" data-act="signout">${ic("logout")}Sign out</button></div>`);
}

export async function doAct(act) {
  if (!act) return;
  const [a, ...rest] = act.split(":");
  const arg = rest.join(":");
  const api = hooks.api;
  switch (a) {
    case "theme":
      setTheme(S.theme === "light" ? "dark" : "light");
      return;
    case "demo":
      closeModal();
      S.demoOpen = !S.demoOpen;
      renderDemo();
      updateDispatcher();
      return;
    case "me":
      openMe();
      return;
    case "signout":
      closeModal();
      CTX.signOut();
      return;
    case "clock": {
      const [op, val] = arg.split(":");
      const body = op === "pause" ? { op: "pause" } : op === "run" ? { op: "run", rate: Number(val) } : { op: "advance", minutes: Number(val) };
      await call(() => api.post("/api/demo/clock", body));
      renderDemo();
      return;
    }
    case "reset":
      openModal(`<h3>Reset the demo?</h3><p>Everything in workspace ${esc(S.clockPayload?.workspace_code || "MAIN")} goes back to Tuesday 15:20, before the cutoff: orders, plans, loads, deliveries and messages. Every signed-in screen follows.</p><div class="iacts" style="margin-top:18px"><button class="dbtn ember" data-act="resetgo">${ic("reset")}Reset now</button><button class="dbtn" data-act="closemodal">Cancel</button></div>`);
      return;
    case "resetgo":
      closeModal();
      setBusy("Resetting the demo…", "Reloading the Tuesday evening state for every role.");
      S.manualPos = {};
      S.lever = null;
      S.leverFor = null;
      S.outlook = null;
      await call(() => api.post("/api/demo/reset"), "Demo reset to Tuesday 15:20.", "blue", "reset");
      return;
    case "dview":
      S.dview = arg;
      if (arg === "lever" && S.plan && !S.leverPending) S.leverPending = S.plan.policy;
      if (arg !== "lever" && S.plan) S.leverPending = S.plan.policy;
      updateDispatcher();
      return;
    case "dfilter":
      S.dfilter = arg;
      cvSync();
      return;
    case "zoom":
      if (arg === "in") zoomBy(1.25);
      else if (arg === "out") zoomBy(0.8);
      else fitView();
      return;
    case "plan": {
      const n = Object.keys(S.orders).length;
      setBusy("Planning both depots…", `${n} orders, ${Object.values(S.vehicles).filter((v) => v.status === "ok").length} vehicles. CP-SAT optimiser with capacity, refrigeration, access, windows and fuel. About 10 seconds.`);
      await call(
        () => api.post("/api/dispatch/plans", { policy: S.plan?.policy || "balanced", publish: false }),
        (r) => `Plan v${r.version} drafted: ${r.kpis.served} of ${r.kpis.orders} served, ${r.kpis.deferred} deferred, ${(r.solver.solve_ms / 1000).toFixed(1)} s.`,
        "blue",
        "layers",
      );
      return;
    }
    case "replan":
      setBusy("Re-planning…", "Same rules, fresh solve. Docks keep the published plan until you publish the new one.");
      await call(() => api.post("/api/dispatch/plans", { policy: S.plan?.policy || "balanced", publish: false }), (r) => `Plan v${r.version} drafted. Review it, then publish.`, "blue", "sync");
      return;
    case "publish":
      if (!S.plan) return;
      await call(() => api.post(`/api/dispatch/plans/${S.plan.id}/publish`), (r) => `Plan v${r.version} published. Docks, drivers and stores have it.`, "green", "send");
      return;
    case "lever":
      S.leverPending = arg;
      updateDispatcher();
      return;
    case "leverapply": {
      if (!S.plan || !S.leverPending || S.leverPending === S.plan.policy) return;
      const pol = S.leverPending;
      setBusy(`Applying “${LEVER_LABEL[pol].label}”…`, S.plan.status === "published" ? "Re-solving and republishing. Stores are told what changed." : "Re-solving the draft.");
      const r = await call(
        () => api.post(`/api/dispatch/plans/${S.plan.id}/policy`, { policy: pol }),
        (x) => `Plan v${x.version} ${x.status === "published" ? "published" : "drafted"} · ${LEVER_LABEL[pol].label}. ${x.kpis.served} served, ${x.kpis.deferred} deferred.`,
        "blue",
        "layers",
      );
      if (r) {
        S.dview = "canvas";
        updateDispatcher();
      }
      return;
    }
    case "closeinsp":
      S.dsel = null;
      cvSync();
      return;
    case "dismiss":
      await call(() => api.post(`/api/dispatch/feed/${arg}/dismiss`));
      return;
    case "locate": {
      S.dview = "canvas";
      updateDispatcher();
      const k = "t-" + arg;
      if (CV.els[k]) {
        S.dsel = k;
        cvSync();
        centerOn(k, 0.85);
      } else toast(`Trip ${esc(arg)} is not on the current plan.`, "", "info");
      return;
    }
    case "shortfall":
      openShortfallModal(arg);
      return;
    case "decide": {
      const [id, key] = arg.split("|");
      closeModal();
      await call(() => api.post(`/api/dispatch/issues/${id}/decide`, { decision: key }), (r) => `${esc(r.text)}. The dock, the driver and the store have been told.`, "green", "check");
      return;
    }
    case "ackissue":
      await call(() => api.post(`/api/dispatch/issues/${arg}/decide`, { decision: "ack" }), "Acknowledged. The driver sees it was handled.", "green", "check");
      return;
    case "deferask":
      openDeferModal(S.orders[arg]);
      return;
    case "deferwith": {
      const [id, reason] = arg.split("|");
      closeModal();
      const o = S.orders[id];
      await call(() => api.post(`/api/dispatch/orders/${o.dbId}/defer`, { reason }), (r) => esc(r.message), "ember", "cal");
      return;
    }
    case "moveask":
      openMoveModal(S.orders[arg]);
      return;
    case "move": {
      const [id, veh, n] = arg.split("|");
      closeModal();
      const o = S.orders[id];
      try {
        const r = await api.post("/api/dispatch/moves", { order_id: o.dbId, vehicle_id: veh, trip_no: Number(n) });
        if (r.ok) toast(esc(r.message), r.queued ? "blue" : "green", r.queued ? "clock" : "check");
        else toast(esc(r.message), "ember", "alert");
      } catch (e) {
        toast(esc(String(e.message || e)), "ember", "alert");
      }
      await CTX.refetch();
      relayout(true, ["o-" + id]);
      return;
    }
    case "split":
      await call(() => api.post(`/api/dispatch/orders/${S.orders[arg].dbId}/split-request`), (r) => esc(r.message), "blue", "split");
      return;
    case "lock": {
      const t = S.trips[arg];
      if (!t) return;
      await call(() => api.post(`/api/dispatch/trips/${t.dbId}/lock`), (r) => (r.locked ? `${t.veh} trip ${t.n} locked. Re-planning won’t move its orders.` : `${t.veh} trip ${t.n} unlocked.`), "blue", "lock");
      renderInspector();
      return;
    }
    case "closemodal":
      closeModal();
      return;
    case "closeorders":
      await call(() => api.post("/api/dispatch/orders/close"), (r) => `Orders closed: ${r.total} for ${dayLabel(S.serviceDate)}, ${r.standing_placed} placed from usual orders.`, "blue", "lock");
      return;
  }
}

