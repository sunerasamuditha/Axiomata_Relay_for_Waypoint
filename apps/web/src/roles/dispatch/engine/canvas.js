/**
 * The dispatcher canvas: two depot globes, every trip in orbit, every order card around its trip,
 * deferred orders drifting in the conflict zone. Drag a card onto a trip to move it (checked by the
 * server against capacity, temperature, access, time and fuel), drop it on the bar to defer it,
 * hover for the window bar and the end-to-end relay, click for the inspector.
 */
import {
  $,
  clamp,
  closeModal,
  dayLabel,
  DEPOTS,
  dk,
  dur,
  esc,
  etaText,
  f1,
  fmt,
  freshMinutes,
  dayMinutes,
  hooks,
  ic,
  isConflict,
  isDark,
  kg,
  M,
  nowMin,
  openModal,
  orderStatus,
  relayHTML,
  S,
  shortIssueFor,
  STATUS_LABEL,
  toast,
  tripOrders,
  tripState,
  vehiclePos,
  windowBar,
} from "./core";

export const SIZE = { o: { w: 184, h: 50 }, t: { w: 370, h: 90 } };
export const G = { CW: 5600, CH: 3500, GLOBE: { PEL: { x: 1900, y: 1950, r: 180 }, KDY: { x: 4250, y: 1950, r: 160 } }, DRIFT: { x: 3080, y: 470, rx: 640, ry: 290 }, sig: "" };
export const CV = { el: null };

/* ------------------------------------------------------------------ geometry (grows with the plan) */

function ringRadius(d) {
  const n = visibleTrips(d).length;
  return Math.max(d === "PEL" ? 560 : 460, (n * 262) / (2 * Math.PI));
}
function computeGeometry() {
  const rp = ringRadius("PEL");
  const rk = ringRadius("KDY");
  const reach = (r) => r + 380;
  const y = Math.max(reach(rp), reach(rk)) + 820;
  const px = reach(rp) + 260;
  const kx = px + reach(rp) + reach(rk) + 160;
  G.GLOBE.PEL = { x: px, y, r: 180 };
  G.GLOBE.KDY = { x: kx, y, r: 160 };
  G.CW = Math.max(5600, kx + reach(rk) + 300);
  G.CH = Math.max(3500, y + Math.max(reach(rp), reach(rk)) + 300);
  G.DRIFT = { x: (px + kx) / 2, y: 470, rx: Math.min(900, Math.max(640, (kx - px) / 2.4)), ry: 290 };
}
function applyGeometry() {
  const sig = [G.CW, G.CH, G.DRIFT.x, G.DRIFT.rx].map(Math.round).join(",");
  if (sig === G.sig || !CV.world) return;
  G.sig = sig;
  CV.world.style.width = G.CW + "px";
  CV.world.style.height = G.CH + "px";
  const svg = $("#cvl", CV.el);
  svg.setAttribute("width", G.CW);
  svg.setAttribute("height", G.CH);
  svg.setAttribute("viewBox", `0 0 ${G.CW} ${G.CH}`);
  const dr = $(".drift", CV.el);
  Object.assign(dr.style, { left: G.DRIFT.x - G.DRIFT.rx + "px", top: G.DRIFT.y - G.DRIFT.ry + "px", width: G.DRIFT.rx * 2 + "px", height: G.DRIFT.ry * 2 + "px" });
  const dl = $(".drift-label", CV.el);
  Object.assign(dl.style, { left: G.DRIFT.x + "px", top: G.DRIFT.y - G.DRIFT.ry + 18 + "px" });
  const nodes = $("#cvn", CV.el);
  nodes.style.width = G.CW + "px";
  nodes.style.height = G.CH + "px";
}

/* ------------------------------------------------------------------ mount */

export function mountCanvas(host) {
  host.innerHTML = `<div class="cv" id="cv">
    <div class="cv-world" id="cvw" style="width:${G.CW}px;height:${G.CH}px">
      <div class="cv-grid"></div>
      <div class="drift"></div>
      <div class="drift-label">Deferred and conflicts<small>They drift here until the lever or a drop resolves them</small></div>
      <svg class="cv-links" id="cvl" width="${G.CW}" height="${G.CH}" viewBox="0 0 ${G.CW} ${G.CH}"><defs><filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter></defs><g id="cvlg"></g><g id="cvdots"></g></svg>
      <div class="cv-nodes" id="cvn"></div>
    </div>
    <div class="cv-ov filters" id="cvf"></div>
    <div class="cv-ov feed" id="cvfeed"></div>
    <div class="cv-ov legend">
      <span><span class="dot" style="background:var(--st-planned)"></span>Planned</span>
      <span><span class="dot" style="background:var(--st-loading)"></span>Loading</span>
      <span><span class="dot" style="background:var(--st-out)"></span>On the road</span>
      <span><span class="dot" style="background:var(--st-delivered)"></span>Delivered</span>
      <span><span class="dot" style="background:var(--st-deferred)"></span>Deferred or conflict</span>
      <span>${ic("snow")} Chilled</span>
      <span style="color:var(--night-ink-3)">Drag a card onto a trip to move it</span>
    </div>
    <div class="cv-ov minimap" id="cvmm"></div>
    <div class="cv-ov zoomer"><button data-act="zoom:in" aria-label="Zoom in">${ic("plus")}</button><button data-act="zoom:out" aria-label="Zoom out">${ic("minus")}</button><button data-act="zoom:fit" aria-label="Fit everything">${ic("maximize")}</button></div>
    <div class="cv-ov dropzone" id="cvdz">${ic("cal")} Drop here to defer to tomorrow</div>
    <div id="cvempty"></div>
    <aside class="inspector" id="insp"></aside>
  </div>`;
  Object.assign(CV, {
    el: $("#cv", host),
    world: $("#cvw", host),
    links: $("#cvlg", host),
    dots: $("#cvdots", host),
    nodes: $("#cvn", host),
    tx: 0,
    ty: 0,
    k: 0.5,
    els: {},
    sig: {},
    pos: {},
    paths: {},
    drag: null,
    pan: null,
    hover: null,
    hc: null,
    dotSig: "",
    needsFit: true,
    opts: {},
  });
  G.sig = "";
  bindCanvas();
  computeGeometry();
  applyGeometry();
  CV.pos = computeLayout();
  cvSync();
  requestAnimationFrame(() => {
    if (CV.el && CV.el.getBoundingClientRect().width) {
      CV.needsFit = false;
      fitView(false);
    }
  });
}

export function sortTrips(a, b) {
  const br = { Fresh: 0, Tech: 1, Style: 2 };
  return (br[a.brand] ?? 3) - (br[b.brand] ?? 3) || (a.district < b.district ? -1 : a.district > b.district ? 1 : 0) || a.n - b.n || (a.veh < b.veh ? -1 : 1);
}
export function visibleTrips(d) {
  return Object.values(S.trips)
    .filter((t) => t.stops.length && (!d || t.depot === d))
    .sort(sortTrips);
}

export function computeLayout() {
  const P = {};
  const fixed = new Set();
  for (const d of ["PEL", "KDY"]) {
    const gp = S.manualPos["g-" + d] || { x: G.GLOBE[d].x, y: G.GLOBE[d].y };
    P["g-" + d] = { ...gp };
    fixed.add("g-" + d);
    const trips = visibleTrips(d);
    const n = trips.length;
    const R = ringRadius(d);
    const a0 = d === "PEL" ? Math.PI * 0.62 : -Math.PI * 0.42;
    trips.forEach((t, i) => {
      const a = a0 + (i * 2 * Math.PI) / Math.max(n, 1);
      const key = "t-" + t.id;
      const tp = S.manualPos[key] || { x: gp.x + Math.cos(a) * R, y: gp.y + Math.sin(a) * R };
      P[key] = { ...tp, a };
      if (S.manualPos[key]) fixed.add(key);
      const k = t.stops.length;
      const sp = Math.min(0.36, 1.6 / Math.max(k, 1));
      t.stops.forEach((oid, j) => {
        const ok = "o-" + oid;
        if (S.manualPos[ok]) {
          P[ok] = { ...S.manualPos[ok] };
          fixed.add(ok);
          return;
        }
        const aa = a + (j - (k - 1) / 2) * sp;
        const rr = 200 + (k > 3 ? (j % 2) * 130 : 0);
        P[ok] = { x: tp.x + Math.cos(aa) * rr, y: tp.y + Math.sin(aa) * rr * 0.92 };
      });
    });
  }
  const defs = Object.values(S.orders).filter((o) => o.deferred);
  const cols = Math.max(4, Math.min(7, Math.ceil(Math.sqrt(defs.length * 2))));
  defs.forEach((o, i) => {
    const ok = "o-" + o.id;
    if (S.manualPos[ok]) {
      P[ok] = { ...S.manualPos[ok] };
      fixed.add(ok);
      return;
    }
    const c = i % cols;
    const r = Math.floor(i / cols);
    P[ok] = { x: G.DRIFT.x + (c - (cols - 1) / 2) * 215 + (r % 2 ? 70 : 0), y: G.DRIFT.y - 60 + r * 96 + (c % 2 ? 26 : 0) };
  });
  relax(P, fixed);
  return P;
}
function nodeSize(key) {
  return key[0] === "o" ? SIZE.o : SIZE.t;
}
function relax(P, fixed) {
  const keys = Object.keys(P).filter((k) => k[0] !== "g");
  for (let it = 0; it < 60; it++) {
    let moved = false;
    for (let i = 0; i < keys.length; i++) {
      const a = keys[i];
      const pa = P[a];
      const sa = nodeSize(a);
      for (const d of ["PEL", "KDY"]) {
        const g = P["g-" + d];
        const gr = G.GLOBE[d].r + 54;
        const dx = pa.x - g.x;
        const dy = pa.y - g.y;
        const dist = Math.hypot(dx, dy) || 1;
        const need = gr + Math.max(sa.w, sa.h) * 0.5;
        if (dist < need && !fixed.has(a)) {
          pa.x = g.x + (dx / dist) * need;
          pa.y = g.y + (dy / dist) * need;
          moved = true;
        }
      }
      for (let j = i + 1; j < keys.length; j++) {
        const b = keys[j];
        const pb = P[b];
        const sb = nodeSize(b);
        const dx = pb.x - pa.x;
        const dy = pb.y - pa.y;
        const ox = (sa.w + sb.w) / 2 + 12 - Math.abs(dx);
        const oy = (sa.h + sb.h) / 2 + 12 - Math.abs(dy);
        if (ox > 0 && oy > 0) {
          const fa = fixed.has(a) ? 0 : a[0] === "t" ? 0.25 : 1;
          const fb = fixed.has(b) ? 0 : b[0] === "t" ? 0.25 : 1;
          if (fa + fb === 0) continue;
          const wa = fa / (fa + fb);
          const wb = fb / (fa + fb);
          if (ox < oy) {
            const s = Math.sign(dx) || 1;
            pa.x -= s * ox * wa;
            pb.x += s * ox * wb;
          } else {
            const s = Math.sign(dy) || 1;
            pa.y -= s * oy * wa;
            pb.y += s * oy * wb;
          }
          moved = true;
        }
      }
    }
    if (!moved) break;
  }
}

/* ------------------------------------------------------------------ filters */

export function orderMatches(o) {
  const f = S.dfilter;
  if (f === "all") return true;
  if (f === "Fresh" || f === "Style" || f === "Tech") return o.brand === f;
  if (f === "chilled") return o.temp === "chilled";
  if (f === "risk") return !o.deferred && !o.delivered && o.risk > 0.45;
  if (f === "deferred") return !!o.deferred || isConflict(o);
  if (f === "offline") {
    const t = S.trips[o.trip];
    return !!t && isDark(t);
  }
  return true;
}

/* ------------------------------------------------------------------ node HTML */

function globeHTML(d) {
  const trips = visibleTrips(d);
  const os = trips.flatMap((t) => t.stops);
  const cold = Object.values(S.vehicles).filter((v) => v.depot === d && v.temp === "reefer");
  const coldOk = cold.filter((v) => v.status === "ok").length;
  const del = os.filter((id) => S.orders[id]?.delivered).length;
  const defN = Object.values(S.orders).filter((o) => o.deferred && o.depot === d).length;
  const dep = DEPOTS[d] || { kind: "", short: d };
  return `<div class="gk">${esc(dep.kind)}</div><div class="gn">${esc(dep.short)}</div><div class="gs">${trips.length} trips · ${os.length} orders<br>${del} delivered · ${defN} deferred</div><div class="gpc"><span>${ic("snow")} ${coldOk}/${cold.length} cold</span></div>`;
}
function tripHTML(t) {
  const v = S.vehicles[t.veh] || { id: t.veh, type: "truck", temp: "ambient", m3: 1 };
  const st = tripState(t);
  const pct = Math.round((t.m3 / v.m3) * 100);
  const pos = vehiclePos(t);
  const dark = isDark(t);
  let stt = "";
  if (dark) stt = `<span class="st warn">${ic("wifiOff")} No signal since ${fmt(t.darkSince)}</span>`;
  else if (st === "planned") stt = `<span class="st">Loads ${fmt(t.depart - 75)}</span>`;
  else if (st === "loading") stt = `<span class="st go">Loading ${t.loading && t.loading.total ? Math.round((t.loading.done / t.loading.total) * 100) + "%" : ""}</span>`;
  else if (st === "loaded") stt = `<span class="st go">Loaded · departs ${fmt(t.depart)}</span>`;
  else if (st === "out") stt = `<span class="st ok">On road · ${pos.idx}/${t.stops.length}</span>`;
  else stt = `<span class="st ok">${ic("check")} Complete</span>`;
  return `<div class="r1">${ic(v.type === "van" ? "van" : "truck")}${v.id} · T${t.n}${v.temp === "reefer" ? ic("snow", "snow") : ""}${t.locked ? ic("lock") : ""}</div>
  <div class="r2">${t.brand} · ${esc(t.district)} · ${esc((t.driver || "").split(" ")[0])}</div>
  <div class="r3"><span class="mbar ${pct > 92 ? "hi" : ""}"><i style="width:${Math.min(100, pct)}%"></i></span>${pct}%</div>
  <div class="r3" style="margin-top:4px">${stt}${dark ? `<span style="margin-left:auto">est. stop ${Math.min(pos.idx + 1, t.stops.length)}</span>` : ""}</div>`;
}
function orderCardHTML(o) {
  const ou = S.outlets[o.outlet] || { name: o.outlet };
  const st = orderStatus(o);
  const t = S.trips[o.trip];
  const dark = t && isDark(t);
  const sf = shortIssueFor(o);
  let flag = "";
  if (o.deferred)
    flag = `<div class="flagline">${o.deferred.kind === "unavoidable" ? "Unavoidable" : "Choice"} · ${esc(o.deferred.code)}<span class="streak">${[0, 1, 2]
      .map((i) => `<i class="${i < o.deferred.streak ? "on" : ""}"></i>`)
      .join("")}</span></div>`;
  else if (o.pendingMove) flag = `<div class="flagline blue">${ic("clock")} Move queued · applies on sync</div>`;
  else if (isConflict(o)) flag = `<div class="flagline">${ic("alert")} Planned after the window</div>`;
  else if (sf) flag = `<div class="flagline amber">${ic("box")} ${sf.status === "open" ? "Short at the dock" : "Short-loaded"}</div>`;
  else if (st === "failed") flag = `<div class="flagline">${ic("x")} Not delivered</div>`;
  else if (st === "partial") flag = `<div class="flagline amber">${ic("box")} Partial delivery</div>`;
  const eta = o.deferred ? dayLabel(nextDayIso()).slice(0, 3) : o.delivered ? "✓ " + fmt(o.deliveredAt) : (dark ? "est " : "~") + fmt(o.eta);
  return `<div class="inner-float"><div class="in"><div class="a">${o.temp === "chilled" ? ic("snow", "snow") : ic("box")}<span class="nm">${esc(ou.name)}</span><span class="sd far-only ${st}"></span></div>
  <div class="b"><span class="sd ${st}"></span><span class="bl">${o.brand[0]}</span>${f1(o.m3)} m³<span class="eta">${eta}</span></div>${flag}</div></div>`;
}
export function nextDayIso() {
  const d = new Date(S.base + 86400000);
  return d.toISOString().slice(0, 10);
}

function ensureEl(key, cls, html, sig) {
  let el = CV.els[key];
  if (!el) {
    el = document.createElement("div");
    el.dataset.key = key;
    CV.nodes.appendChild(el);
    CV.els[key] = el;
  }
  el.className = cls;
  if (CV.sig[key] !== sig) {
    el.innerHTML = html;
    CV.sig[key] = sig;
  }
  return el;
}

/** Bring the DOM in line with S (creates, updates and removes nodes; keeps positions). */
export function cvSync() {
  if (!CV.el) return;
  computeGeometry();
  applyGeometry();
  const want = new Set();
  const newPos = computeLayout();
  for (const d of ["PEL", "KDY"]) {
    const k = "g-" + d;
    want.add(k);
    const h = globeHTML(d);
    const el = ensureEl(k, "globe " + (d === "KDY" ? "kdy" : ""), h, h);
    const r = G.GLOBE[d].r;
    el.style.width = el.style.height = r * 2 + "px";
    if (!CV.pos[k] || !S.manualPos[k]) CV.pos[k] = newPos[k];
  }
  for (const d of ["PEL", "KDY"]) {
    const ws = Object.values(S.vehicles)
      .filter((v) => v.depot === d && v.status === "workshop")
      .map((v) => v.id.replace("VEH", ""));
    if (ws.length) {
      const k = "w-" + d;
      want.add(k);
      ensureEl(k, "wshop", `${ic("wrench")} In workshop: VEH ${ws.join(", ")}`, "ws" + ws.join());
      CV.pos[k] = { x: CV.pos["g-" + d].x, y: CV.pos["g-" + d].y + G.GLOBE[d].r + 30 };
    }
  }
  visibleTrips().forEach((t) => {
    const k = "t-" + t.id;
    want.add(k);
    const st = tripState(t);
    const dark = isDark(t);
    const dim = S.dfilter !== "all" && !t.stops.some((id) => S.orders[id] && orderMatches(S.orders[id]));
    const html = tripHTML(t);
    ensureEl(k, `tn ${st === "out" ? "out" : ""} ${dark ? "dark" : ""} ${dim ? "dim" : ""} ${S.dsel === k ? "sel" : ""}`, html, html);
    if (!CV.pos[k]) CV.pos[k] = newPos[k];
  });
  Object.values(S.orders).forEach((o) => {
    if (!o.deferred && !(o.trip && S.trips[o.trip] && S.trips[o.trip].stops.includes(o.id))) return;
    const k = "o-" + o.id;
    want.add(k);
    const st = orderStatus(o);
    const cls = `oc s-${st} ${o.deferred ? "def" : ""} ${isConflict(o) ? "conflict" : ""} ${["delivered", "received", "partial"].includes(st) ? "done" : ""} ${!o.deferred && !o.delivered && o.risk > 0.45 ? "risk" : ""} ${orderMatches(o) ? "" : "dim"} ${S.dsel === k ? "sel" : ""}`;
    const html = orderCardHTML(o);
    ensureEl(k, cls, html, html);
    if (!CV.pos[k]) CV.pos[k] = newPos[k] || { x: G.DRIFT.x, y: G.DRIFT.y };
  });
  Object.keys(CV.els).forEach((k) => {
    if (!want.has(k)) {
      CV.els[k].remove();
      delete CV.els[k];
      delete CV.sig[k];
      delete CV.pos[k];
    }
  });
  placeAll();
  drawLinks();
  drawFilters();
  drawFeed();
  drawMinimap();
  drawEmpty();
  renderInspector();
}

/** Animate every node to its computed place (after a plan change or a move). */
export function relayout(animate = true, clearKeys = []) {
  if (!CV.el) return;
  clearKeys.forEach((k) => delete S.manualPos[k]);
  computeGeometry();
  applyGeometry();
  const target = computeLayout();
  if (!animate) {
    Object.keys(target).forEach((k) => {
      if (CV.els[k]) CV.pos[k] = target[k];
    });
    placeAll();
    drawLinks();
    drawMinimap();
    return;
  }
  const from = {};
  Object.keys(target).forEach((k) => (from[k] = CV.pos[k] ? { ...CV.pos[k] } : { ...target[k] }));
  const t0 = performance.now();
  const D = 650;
  const step = (now) => {
    if (!CV.el) return;
    const p = clamp((now - t0) / D, 0, 1);
    const e = 1 - Math.pow(1 - p, 3);
    Object.keys(target).forEach((k) => {
      if (!CV.els[k]) return;
      const a = from[k];
      const b = target[k];
      CV.pos[k] = { x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e };
    });
    placeAll();
    drawLinks();
    if (p < 1) requestAnimationFrame(step);
    else drawMinimap();
  };
  requestAnimationFrame(step);
}
export function placeAll() {
  Object.entries(CV.els).forEach(([k, el]) => {
    const p = CV.pos[k];
    if (p) {
      el.style.left = p.x + "px";
      el.style.top = p.y + "px";
    }
  });
}
export function applyView() {
  CV.world.style.transform = `translate(${CV.tx}px,${CV.ty}px) scale(${CV.k})`;
  CV.el.classList.toggle("far", CV.k < 0.62);
  CV.el.classList.toggle("vfar", CV.k < 0.34);
  drawMinimap();
}

/* ------------------------------------------------------------------ links */

function linkPath(a, b, bend = 0.18) {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const cx = mx - dy * bend;
  const cy = my + dx * bend;
  return `M${a.x.toFixed(1)},${a.y.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`;
}
function setPath(key, d, stroke, w, dash, op) {
  let p = CV.paths[key];
  if (!p) {
    p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.id = "lk-" + key;
    p.setAttribute("fill", "none");
    p.setAttribute("stroke-linecap", "round");
    CV.links.appendChild(p);
    CV.paths[key] = p;
  }
  p.setAttribute("d", d);
  p.style.stroke = stroke;
  p.setAttribute("stroke-width", w);
  p.setAttribute("stroke-dasharray", dash || "");
  p.setAttribute("opacity", op);
  p._seen = true;
}
export function drawLinks() {
  if (!CV.el) return;
  Object.values(CV.paths).forEach((p) => (p._seen = false));
  const dots = [];
  visibleTrips().forEach((t) => {
    const g = CV.pos["g-" + t.depot];
    const tp = CV.pos["t-" + t.id];
    if (!g || !tp) return;
    const st = tripState(t);
    const dark = isDark(t);
    const col = dark ? "var(--c-warn)" : st === "out" ? "var(--c-road)" : st === "done" ? "var(--c-done)" : "var(--c-plan)";
    const dim = S.dfilter !== "all" && !t.stops.some((id) => S.orders[id] && orderMatches(S.orders[id]));
    setPath("gt-" + t.id, linkPath(g, tp, 0.12), col, st === "out" ? 3 : 2.2, dark ? "8 8" : "", dim ? 0.12 : 0.7);
    if (st === "out" && !dark) dots.push(t.id);
    t.stops.forEach((id) => {
      const o = S.orders[id];
      const op = CV.pos["o-" + id];
      if (!o || !op) return;
      const conf = isConflict(o);
      setPath("to-" + id, linkPath(tp, op, 0.1), conf ? "var(--c-warn)" : o.delivered ? "var(--c-done)" : "var(--c-order)", 1.4, conf ? "5 6" : "", orderMatches(o) ? (o.delivered ? 0.65 : 0.45) : 0.08);
    });
  });
  Object.values(S.orders)
    .filter((o) => o.deferred)
    .forEach((o) => {
      const g = CV.pos["g-" + o.depot];
      const op = CV.pos["o-" + o.id];
      if (!g || !op) return;
      const dx = op.x - g.x;
      const dy = op.y - g.y;
      const dd = Math.hypot(dx, dy) || 1;
      const r = (G.GLOBE[o.depot]?.r ?? 170) + 10;
      setPath("df-" + o.id, linkPath({ x: g.x + (dx / dd) * r, y: g.y + (dy / dd) * r }, op, 0.08), "var(--c-warn)", 1.4, "3 9", orderMatches(o) ? 0.4 : 0.06);
    });
  Object.entries(CV.paths).forEach(([k, p]) => {
    if (!p._seen) {
      p.remove();
      delete CV.paths[k];
    }
  });
  const sig = dots.join();
  if (CV.dotSig !== sig) {
    CV.dotSig = sig;
    CV.dots.innerHTML = dots
      .map(
        (id, i) =>
          `<circle r="6" style="fill:var(--c-road)" filter="url(#glow)"><animateMotion dur="${3.2 + (i % 4) * 0.5}s" repeatCount="indefinite" rotate="auto"><mpath href="#lk-gt-${id}"/></animateMotion></circle><circle r="3.5" style="fill:var(--c-dot)"><animateMotion dur="${3.2 + (i % 4) * 0.5}s" repeatCount="indefinite"><mpath href="#lk-gt-${id}"/></animateMotion></circle>`,
      )
      .join("");
  }
}

/* ------------------------------------------------------------------ overlays on the canvas */

function drawFilters() {
  const all = Object.values(S.orders).filter((o) => o.deferred || (o.trip && S.trips[o.trip] && S.trips[o.trip].stops.includes(o.id)));
  const cnt = (f) => {
    const keep = S.dfilter;
    S.dfilter = f;
    const n = all.filter(orderMatches).length;
    S.dfilter = keep;
    return n;
  };
  const fs = [
    ["all", "All"],
    ["Fresh", "Fresh"],
    ["Style", "Style"],
    ["Tech", "Tech"],
    ["chilled", "Chilled"],
    ["risk", "At risk"],
    ["deferred", "Deferred"],
    ["offline", "No signal"],
  ];
  $("#cvf", CV.el).innerHTML = all.length
    ? fs.map(([k, l]) => `<button class="fchip ${S.dfilter === k ? "on" : ""}" data-act="dfilter:${k}">${l}<span class="n">${cnt(k)}</span></button>`).join("")
    : "";
}
export function drawFeed() {
  if (!CV.el) return;
  const all = S.feed;
  const items = all.slice(0, 3);
  $("#cvfeed", CV.el).innerHTML =
    items
      .map(
        (f) =>
          `<div class="fcard ${f.kind}"><div class="h">${ic(f.icon)}${esc(f.title)}<span class="t">${f.at}</span></div>${f.body ? `<p>${f.body}</p>` : ""}<div class="acts">${(f.act || [])
            .map(([l, a], i) => `<button class="dbtn ${i === 0 && f.kind === "ember" ? "ember" : ""}" style="height:30px;font-size:12.5px" data-act="${esc(a)}">${esc(l)}</button>`)
            .join("")}<button class="dbtn" style="height:30px;font-size:12.5px" data-act="dismiss:${f.id}">Dismiss</button></div></div>`,
      )
      .join("") + (all.length > 3 ? `<div class="fcard" style="padding:8px 12px">${all.length - 3} older alert${all.length - 3 === 1 ? "" : "s"} below. Dismiss one to see more.</div>` : "");
}
export function drawMinimap() {
  const mm = CV.el && $("#cvmm", CV.el);
  if (!mm) return;
  const sx = 190 / G.CW;
  const sy = 118 / G.CH;
  const r = CV.el.getBoundingClientRect();
  const vx = -CV.tx / CV.k;
  const vy = -CV.ty / CV.k;
  const vw = r.width / CV.k;
  const vh = r.height / CV.k;
  let s = `<svg viewBox="0 0 190 118"><ellipse cx="${G.DRIFT.x * sx}" cy="${G.DRIFT.y * sy}" rx="${G.DRIFT.rx * sx}" ry="${G.DRIFT.ry * sy}" style="fill:var(--mm-drift)"/>`;
  ["PEL", "KDY"].forEach((d) => {
    const p = CV.pos["g-" + d];
    if (p) s += `<circle cx="${p.x * sx}" cy="${p.y * sy}" r="${G.GLOBE[d].r * sx * 1.6}" style="fill:var(--mm-globe)"/>`;
  });
  Object.entries(CV.pos).forEach(([k, p]) => {
    if (k[0] === "o") {
      const o = S.orders[k.slice(2)];
      s += `<rect x="${p.x * sx - 1.5}" y="${p.y * sy - 1}" width="3" height="2" style="fill:${o && o.deferred ? "var(--c-warn)" : "var(--mm-order)"}"/>`;
    }
  });
  s += `<rect x="${vx * sx}" y="${vy * sy}" width="${vw * sx}" height="${vh * sy}" fill="none" style="stroke:var(--mm-view)" stroke-width="1"/></svg>`;
  mm.innerHTML = s;
}
function drawEmpty() {
  const box = $("#cvempty", CV.el);
  if (!box) return;
  if (Object.keys(S.trips).length || S.phase === "draft" || S.phase === "published") {
    box.innerHTML = "";
    return;
  }
  const orders = Object.values(S.orders);
  const chilled = orders.filter((o) => o.temp === "chilled").reduce((a, o) => a + o.m3, 0);
  const closed = S.phase === "closed";
  const cut = S.clockPayload?.cutoff;
  const left = cut ? (M(cut) ?? 0) - nowMin() : 0;
  box.innerHTML = `<div class="cv-empty"><div class="box"><div class="big">${ic("layers")}</div>
    <h2>${closed ? "Orders are closed. Ready to plan." : `Orders for ${dayLabel(S.serviceDate)} close at 16:00`}</h2>
    <p>${closed ? "Plan every order across both depots in one go. The optimiser respects capacity, refrigeration, access, delivery windows and fuel, and explains every deferral." : `${left > 0 ? `${dur(left)} to the cutoff. ` : ""}Stores are still ordering. Close now to plan early, or wait for the cutoff: anything a store has not changed is placed from its usual order.`}</p>
    <div class="kv3"><div><b>${orders.length}</b><small>orders</small></div><div><b>${f1(chilled)}</b><small>m³ chilled</small></div><div><b>${new Set(orders.map((o) => o.outlet)).size}</b><small>outlets</small></div></div>
    <button class="dbtn primary" data-act="plan">${ic("layers")}${closed ? "Plan now" : "Close orders and plan"}</button></div></div>`;
}

/* ------------------------------------------------------------------ view */

export function fitView(anim = true) {
  if (!CV.el) return;
  const r = CV.el.getBoundingClientRect();
  const ps = Object.entries(CV.pos)
    .filter(([k]) => CV.els[k])
    .map(([, p]) => p);
  if (!ps.length) return;
  const x0 = Math.min(...ps.map((p) => p.x)) - 200;
  const x1 = Math.max(...ps.map((p) => p.x)) + 200;
  const y0 = Math.min(...ps.map((p) => p.y)) - 240;
  const y1 = Math.max(...ps.map((p) => p.y)) + 200;
  const insp = S.dsel ? 396 : 0;
  const left = r.width > 1100 && S.feed.length ? 320 : 0;
  const k = clamp(Math.min((r.width - insp - left - 40) / (x1 - x0), (r.height - 170) / (y1 - y0)), 0.12, 1.2);
  const tx = left + (r.width - insp - left - (x1 - x0) * k) / 2 - x0 * k;
  const ty = 64 + (r.height - 150 - (y1 - y0) * k) / 2 - y0 * k;
  animateView(tx, ty, k, anim);
}
function animateView(tx, ty, k, anim = true) {
  if (!anim) {
    CV.tx = tx;
    CV.ty = ty;
    CV.k = k;
    applyView();
    return;
  }
  const a = { tx: CV.tx, ty: CV.ty, k: CV.k };
  const t0 = performance.now();
  const D = 500;
  const step = (now) => {
    if (!CV.el) return;
    const p = clamp((now - t0) / D, 0, 1);
    const e = 1 - Math.pow(1 - p, 3);
    CV.tx = a.tx + (tx - a.tx) * e;
    CV.ty = a.ty + (ty - a.ty) * e;
    CV.k = a.k + (k - a.k) * e;
    applyView();
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
export function zoomBy(f, cx, cy) {
  const r = CV.el.getBoundingClientRect();
  if (cx == null) {
    cx = r.width / 2;
    cy = r.height / 2;
  }
  const k2 = clamp(CV.k * f, 0.1, 1.6);
  const wx = (cx - CV.tx) / CV.k;
  const wy = (cy - CV.ty) / CV.k;
  CV.k = k2;
  CV.tx = cx - wx * k2;
  CV.ty = cy - wy * k2;
  applyView();
}
export function centerOn(key, k) {
  const p = CV.pos[key];
  if (!p) return;
  const r = CV.el.getBoundingClientRect();
  const kk = k || Math.max(CV.k, 0.8);
  const insp = S.dsel ? 396 : 0;
  animateView((r.width - insp) / 2 - p.x * kk, r.height / 2 - p.y * kk, kk);
  const el = CV.els[key];
  if (el && key[0] === "o") {
    el.classList.add("hl");
    setTimeout(() => el.classList.remove("hl"), 2200);
  }
}

/* ------------------------------------------------------------------ interaction */

function bindCanvas() {
  const el = CV.el;
  el.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomBy(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    },
    { passive: false },
  );
  el.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (e.target.closest(".cv-ov,.inspector,.hovercard,.cv-empty")) return;
    const node = e.target.closest(".oc,.tn,.globe");
    hideHover();
    if (node) {
      const key = node.dataset.key;
      const kids = [];
      if (key[0] === "t") {
        const t = S.trips[key.slice(2)];
        if (t) t.stops.forEach((id) => kids.push("o-" + id));
      }
      if (key[0] === "g") {
        const d = key.slice(2);
        kids.push("w-" + d);
        visibleTrips(d).forEach((t) => {
          kids.push("t-" + t.id);
          t.stops.forEach((id) => kids.push("o-" + id));
        });
      }
      CV.drag = { key, node, sx: e.clientX, sy: e.clientY, moved: false, orig: { ...CV.pos[key] }, kids: kids.filter((k) => CV.pos[k]).map((k) => [k, { ...CV.pos[k] }]), pid: e.pointerId };
      node.setPointerCapture(e.pointerId);
    } else {
      CV.pan = { sx: e.clientX, sy: e.clientY, tx: CV.tx, ty: CV.ty };
      el.classList.add("panning");
      el.setPointerCapture(e.pointerId);
    }
  });
  el.addEventListener("pointermove", (e) => {
    if (CV.pan) {
      CV.tx = CV.pan.tx + e.clientX - CV.pan.sx;
      CV.ty = CV.pan.ty + e.clientY - CV.pan.sy;
      applyView();
      return;
    }
    const d = CV.drag;
    if (d) {
      const dx = (e.clientX - d.sx) / CV.k;
      const dy = (e.clientY - d.sy) / CV.k;
      if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) < 5) return;
      if (!d.moved) {
        d.moved = true;
        d.node.classList.add("dragging");
        if (d.key[0] === "o") startOrderDrag(d);
      }
      CV.pos[d.key] = { x: d.orig.x + dx, y: d.orig.y + dy };
      d.kids.forEach(([k, p]) => (CV.pos[k] = { x: p.x + dx, y: p.y + dy }));
      placeAll();
      drawLinks();
      if (d.key[0] === "o") trackOrderDrag(d, e);
      return;
    }
    const node = e.target.closest(".oc,.tn");
    if (node && !e.target.closest(".cv-ov")) {
      if (CV.hover !== node.dataset.key) {
        CV.hover = node.dataset.key;
        showHover(node);
      }
    } else if (CV.hover) hideHover();
  });
  const end = (e) => {
    if (CV.pan) {
      CV.pan = null;
      el.classList.remove("panning");
      drawMinimap();
      return;
    }
    const d = CV.drag;
    if (!d) return;
    CV.drag = null;
    d.node.classList.remove("dragging");
    if (!d.moved) {
      selectNode(d.key);
      return;
    }
    if (d.key[0] === "o") {
      endOrderDrag(d, e);
      return;
    }
    S.manualPos[d.key] = { ...CV.pos[d.key] };
    d.kids.forEach(([k]) => {
      S.manualPos[k] = { ...CV.pos[k] };
    });
    drawMinimap();
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  el.addEventListener("pointerleave", () => {
    if (!CV.drag) hideHover();
  });
}

/** Where can this order go? Asked once per drag (the server checks every rule). */
async function moveOptions(o) {
  const c = CV.opts[o.id];
  if (c && Date.now() - c.at < 8000) return c.list;
  const list = await hooks.api.get(`/api/dispatch/moves/options?order_id=${o.dbId}`);
  CV.opts[o.id] = { at: Date.now(), list };
  return list;
}
function optFor(list, t) {
  return (list || []).find((x) => x.vehicle === t.veh && x.trip_no === t.n);
}
function structuralReason(o, t) {
  const v = S.vehicles[t.veh];
  const ou = S.outlets[o.outlet];
  if (o.trip === t.id) return { same: true, msg: "Already on this trip." };
  if (o.delivered) return { msg: `${ou.name} was already delivered at ${fmt(o.deliveredAt)}.` };
  if (["out", "done"].includes(t.status)) return { msg: `${v.id} has already left the depot. Choose a trip that is still loading.` };
  if (t.status === "loaded") return { msg: `${v.id} is sealed and released. Choose a trip that is still loading.` };
  if (o.depot !== t.depot) return { msg: `${v.id} is based at ${DEPOTS[t.depot]?.name}. ${ou.name} is served from ${DEPOTS[o.depot]?.name}.` };
  if (o.brand !== t.brand || o.district !== t.district) return { msg: `One brand and one district per trip. ${v.id} trip ${t.n} carries ${t.brand} for ${t.district}.` };
  if (o.temp === "chilled" && v.temp !== "reefer") return { msg: `Chilled goods need a refrigerated vehicle. ${v.id} is a dry ${v.type}.` };
  if (ou.park === "van" && v.type !== "van") return { msg: `${ou.name} is van-only. Trucks can’t reach it.` };
  return null;
}
function startOrderDrag(d) {
  const o = S.orders[d.key.slice(2)];
  $("#cvdz", CV.el).classList.toggle("show", !o.deferred && !o.delivered);
  visibleTrips().forEach((t) => {
    const el = CV.els["t-" + t.id];
    if (!el) return;
    const sr = structuralReason(o, t);
    el.classList.toggle("target-no", !!sr && !sr.same);
  });
  d.opts = null;
  moveOptions(o)
    .then((list) => {
      d.opts = list;
      if (!CV.drag || CV.drag !== d) return;
      visibleTrips().forEach((t) => {
        const el = CV.els["t-" + t.id];
        if (!el) return;
        const op = optFor(list, t);
        el.classList.toggle("target-ok", !!op && op.ok);
        el.classList.toggle("target-no", !op || !op.ok);
      });
    })
    .catch(() => undefined);
  hideHover();
}
function nearestTrip(wp) {
  let best = null;
  let bd = 1e9;
  visibleTrips().forEach((t) => {
    const p = CV.pos["t-" + t.id];
    if (!p) return;
    const dd = Math.hypot(p.x - wp.x, p.y - wp.y);
    if (dd < bd) {
      bd = dd;
      best = t;
    }
  });
  return bd < 140 ? best : null;
}
function overDrop(e) {
  const r = $("#cvdz", CV.el).getBoundingClientRect();
  return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
}
function trackOrderDrag(d, e) {
  const t = nearestTrip(CV.pos[d.key]);
  visibleTrips().forEach((x) => {
    const el = CV.els["t-" + x.id];
    if (el) el.classList.toggle("target-hover", !!t && x.id === t.id);
  });
  $("#cvdz", CV.el).classList.toggle("hover", overDrop(e));
}
function clearTargets() {
  visibleTrips().forEach((t) => {
    const el = CV.els["t-" + t.id];
    if (el) el.classList.remove("target-ok", "target-no", "target-hover");
  });
  $("#cvdz", CV.el).classList.remove("show", "hover");
}
function endOrderDrag(d, e) {
  const o = S.orders[d.key.slice(2)];
  const dropDefer = overDrop(e) && !o.deferred && !o.delivered;
  const t = nearestTrip(CV.pos[d.key]);
  clearTargets();
  if (dropDefer) {
    CV.pos[d.key] = d.orig;
    placeAll();
    drawLinks();
    openDeferModal(o);
    return;
  }
  if (t && t.id !== o.trip) {
    const sr = structuralReason(o, t);
    if (sr) {
      toast(esc(sr.msg), "ember", "alert");
      CV.pos[d.key] = d.orig;
      relayout(true);
      return;
    }
    const op = optFor(d.opts, t);
    if (d.opts && op && !op.ok) {
      toast(esc(op.message), "ember", "alert");
      CV.pos[d.key] = d.orig;
      relayout(true);
      return;
    }
    hooks.act(`move:${o.id}|${t.veh}|${t.n}`);
    return;
  }
  S.manualPos[d.key] = { ...CV.pos[d.key] };
  drawMinimap();
}

/* ------------------------------------------------------------------ hover card */

function showHover(node) {
  hideHover(true);
  const key = node.dataset.key;
  const hc = document.createElement("div");
  hc.className = "hovercard " + dk();
  const html = key[0] === "o" ? (S.orders[key.slice(2)] ? orderHoverHTML(S.orders[key.slice(2)]) : "") : S.trips[key.slice(2)] ? tripHoverHTML(S.trips[key.slice(2)]) : "";
  if (!html) return;
  hc.innerHTML = html;
  CV.el.appendChild(hc);
  CV.hc = hc;
  const r = node.getBoundingClientRect();
  const cr = CV.el.getBoundingClientRect();
  const hr = hc.getBoundingClientRect();
  let x = r.right - cr.left + 14;
  if (x + hr.width > cr.width - 12) x = r.left - cr.left - hr.width - 14;
  let y = r.top - cr.top + r.height / 2 - hr.height / 2;
  y = clamp(y, 72, cr.height - hr.height - 86);
  hc.style.left = Math.max(12, x) + "px";
  hc.style.top = y + "px";
}
export function hideHover(keepKey) {
  if (CV.hc) {
    CV.hc.remove();
    CV.hc = null;
  }
  if (!keepKey) CV.hover = null;
}
function orderHoverHTML(o) {
  const ou = S.outlets[o.outlet];
  const t = S.trips[o.trip];
  const st = orderStatus(o);
  const est = t && isDark(t) && st === "out";
  const sf = shortIssueFor(o);
  let h = `<div class="spread"><h4>${esc(ou.name)}</h4><span class="chip ${o.deferred ? "ember" : ["delivered", "received"].includes(st) ? "green" : st === "failed" ? "ember" : "blue"}">${STATUS_LABEL[st] || st}</span></div>
  <div class="meta"><span class="mono">${o.ref}</span> · ${o.brand} · ${esc(ou.district)} · ${o.temp === "chilled" ? "Chilled" : "Ambient"} · ${ou.dock.replace("_", " ")}${ou.park === "van" ? " · van-only" : ""}</div>`;
  if (o.deferred) {
    h += `<div class="why"><b>${o.deferred.kind === "unavoidable" ? "Unavoidable" : "Policy choice"} · ${esc(o.deferred.code)}.</b> ${esc(o.deferred.text)}</div>
    <div class="sec"><div class="eyebrow">Skip streak</div><div class="row"><span class="streakrow">${[0, 1, 2, 3, 4].map((i) => `<i class="${i >= 5 - o.deferred.streak ? "skip" : "ok"}"></i>`).join("")}</span><span class="meta" style="margin:0">${o.deferred.streak > 1 ? "Skipped " + o.deferred.streak + " runs in a row" : "First skip"}${o.daysSince ? " · last served " + o.daysSince + " days ago" : ""}</span></div></div>`;
  } else if (t) {
    const pos = vehiclePos(t);
    const seq = t.stops.indexOf(o.id) + 1;
    h += `<div class="sec"><div class="eyebrow">Delivery window and arrival</div>${windowBar(o, { est })}</div>
    <div class="sec"><div class="eyebrow">End to end</div>${relayHTML(o)}</div>
    <div class="kvs"><div><b>${o.delivered ? fmt(o.deliveredAt) : "~" + fmt(o.eta)}</b><small>${o.delivered ? "Arrived" : est ? "Estimated" : "Predicted"}</small></div><div><b>${Math.round(o.risk * 100)}%</b><small>Late risk</small></div><div><b>${seq}/${t.stops.length}</b><small>Stop</small></div></div>
    <div class="sec meta">${t.veh} trip ${t.n} · ${esc(t.driver)} · ${st === "out" ? (est ? `No signal since ${fmt(t.darkSince)}, estimated at stop ${Math.min(pos.idx + 1, t.stops.length)}` : `vehicle at stop ${Math.min(pos.idx + 1, t.stops.length)} of ${t.stops.length}`) : STATUS_LABEL[st] || st}</div>`;
    if (isConflict(o)) h += `<div class="why"><b>Planned after the window.</b> The plan reaches it about ${fmt(o.planned)}, after the ${fmt(ou.close)} close. Move it to another trip or defer it.</div>`;
    if (sf) h += `<div class="why"><b>${sf.status === "open" ? "Short at the dock." : "Short-loaded."}</b> ${sf.qty} × ${esc(lineName(o, sf.line))} ${sf.kind.replace("_at_dock", "").replace("short", "missing").replace("wrong", "wrong item")}. ${sf.decision_text ? "Decision: " + esc(sf.decision_text) + "." : "Waiting for your decision."}</div>`;
    if (o.pendingMove) h += `<div class="why"><b>Move queued.</b> Goes to ${o.pendingMove.to} trip ${o.pendingMove.trip_no} when ${t.veh} syncs, unless it is delivered first.</div>`;
  }
  h += `<div class="kvs"><div><b>${o.units}</b><small>${o.brand === "Tech" ? "items" : "cases"}</small></div><div><b>${f1(o.m3)}</b><small>m³</small></div><div><b>${kg(o.kg)}</b><small>kg</small></div></div>`;
  return h;
}
function lineName(o, lineId) {
  return (o.lines.find((l) => l.id === lineId) || { name: "items" }).name;
}
function tripHoverHTML(t) {
  const v = S.vehicles[t.veh];
  const st = tripState(t);
  const budget = t.brand === "Fresh" ? 270 : 480;
  const used = t.brand === "Fresh" ? freshMinutes(v.id) : dayMinutes(v.id);
  return `<div class="spread"><h4>${v.id} · trip ${t.n}</h4><span class="chip blue">${STATUS_LABEL[st] || st}</span></div>
  <div class="meta">${v.temp === "reefer" ? "Refrigerated" : "Dry"} ${v.type} · ${t.brand} · ${esc(t.district)} · ${esc(t.driver)} · departs ${fmt(t.departedAt ?? t.depart)}</div>
  ${gauge("Volume", t.m3, v.m3, "m³")}${gauge("Weight", t.kg, v.kg, "kg")}${gauge(t.brand === "Fresh" ? "Fresh window (all trips)" : "Daytime window", used, budget, "min")}
  <div class="sec"><div class="eyebrow">Stops</div>${tripOrders(t)
    .map((o, i) => `<div class="meta" style="display:flex;justify-content:space-between"><span>${i + 1}. ${esc(S.outlets[o.outlet].name)}</span><span class="num">${o.delivered ? "✓ " + fmt(o.deliveredAt) : "~" + fmt(o.eta)}</span></div>`)
    .join("")}</div>`;
}
export function gauge(l, a, b, u) {
  const p = b ? (a / b) * 100 : 0;
  const v = (x) => (u === "kg" ? kg(x) : u === "min" || u === "L" ? Math.round(x) : f1(x));
  return `<div class="gauge"><div class="spread"><span>${l}</span><b>${v(a)} / ${v(b)} ${u}</b></div><div class="bar ${p > 92 ? "hi" : ""}"><i style="width:${Math.min(100, p)}%"></i></div></div>`;
}

/* ------------------------------------------------------------------ inspector */

export function selectNode(key) {
  if (key[0] === "g") {
    centerOn(key, 0.7);
    return;
  }
  S.dsel = key;
  cvSync();
}
export function renderInspector() {
  const el = CV.el && $("#insp", CV.el);
  if (!el) return;
  const key = S.dsel;
  if (!key || !CV.els[key]) {
    el.classList.remove("open");
    CV.el.classList.remove("inspector-open");
    return;
  }
  el.classList.add("open");
  CV.el.classList.add("inspector-open");
  let h = `<button class="x" data-act="closeinsp" aria-label="Close">${ic("x")}</button>`;
  if (key[0] === "o") h += orderInspector(S.orders[key.slice(2)]);
  else h += tripInspector(S.trips[key.slice(2)]);
  el.innerHTML = h;
}
function orderInspector(o) {
  const ou = S.outlets[o.outlet];
  const t = S.trips[o.trip];
  const st = orderStatus(o);
  const sf = shortIssueFor(o);
  let h = `<div class="eyebrow" style="color:var(--night-ink-3)">Order · <span class="mono">${o.ref}</span></div><h3>${esc(ou.name)}</h3><div class="meta">${o.brand} · ${esc(ou.district)} · ${o.temp === "chilled" ? "Chilled" : "Ambient"} · window ${fmt(ou.open)}–${fmt(ou.close)}${ou.manager ? " · " + esc(ou.manager) : ""}</div>
  <div class="row" style="margin-top:12px;flex-wrap:wrap"><span class="chip ${o.deferred || st === "failed" ? "ember" : "blue"}">${STATUS_LABEL[st] || st}</span>${t ? `<span class="chip">${t.veh} · T${t.n}</span>` : ""}${!o.deferred && !o.delivered ? `<span class="chip ${o.risk > 0.45 ? "amber" : "green"}">Late risk ${Math.round(o.risk * 100)}%</span>` : ""}${o.channel === "carryover" ? `<span class="chip ember">Carried over</span>` : ""}</div>`;
  if (o.deferred) {
    const cost = o.deferred.cost || {};
    h += `<div class="why-dark"><b>${o.deferred.kind === "unavoidable" ? "Unavoidable" : "Policy choice"} · ${esc(o.deferred.code)}.</b> ${esc(o.deferred.text)}</div>
    ${cost.displaced_names && cost.displaced_names.length ? `<p class="meta">Serving it today would push off: ${cost.displaced_names.map(esc).join(", ")}.</p>` : ""}
    <div class="sec"><div class="eyebrow">What you can do</div><p class="meta" style="margin:0">Drag this card onto a trip that glows green, pick a slot below, or change the policy on the deferral lever.</p>
    <div class="iacts"><button class="dbtn primary" data-act="moveask:${o.id}">${ic("arrowR")}Find a vehicle…</button>${o.deferred.code === "Too big" || o.deferred.code === "Too heavy" ? `<button class="dbtn ember" data-act="split:${o.id}">${ic("split")}Ask store to split</button>` : ""}<button class="dbtn" data-act="dview:lever">${ic("lever")}Open the lever</button></div></div>`;
  } else if (t) {
    h += `<div class="sec"><div class="eyebrow">Delivery window</div>${windowBar(o, { est: isDark(t) && st === "out" })}</div><div class="sec"><div class="eyebrow">End to end</div>${relayHTML(o)}</div>`;
    if (isConflict(o)) h += `<div class="why-dark"><b>Planned after the window.</b> The plan reaches it about ${fmt(o.planned)}, after the ${fmt(ou.close)} close. Another trip may reach it in time.</div>`;
    if (o.pendingMove) h += `<div class="why-dark"><b>Move queued.</b> To ${o.pendingMove.to} trip ${o.pendingMove.trip_no} when ${t.veh} reconnects. If it is delivered first, the delivery wins and the move is cancelled.</div>`;
    if (sf)
      h += `<div class="why-dark"><b>${sf.status === "open" ? "Short at the dock" : "Shortfall decided"}.</b> ${sf.qty} × ${esc(lineName(o, sf.line))}, flagged by ${esc(sf.raised_by)} at ${fmt(M(sf.raised_at))}. ${sf.decision_text ? esc(sf.decision_text) + "." : ""}</div>${sf.status === "open" ? `<div class="iacts"><button class="dbtn ember" data-act="shortfall:${sf.id}">${ic("box")}Decide now</button></div>` : ""}`;
  }
  h += `<div class="sec"><div class="eyebrow">Lines</div><div class="ilines">${o.lines
    .map((l) => {
      const got = l.received_qty ?? l.delivered_qty;
      return `<div><span>${esc(l.name)}${l.receipt_issue ? ` <span class="chip ember" style="height:20px">${esc(l.receipt_issue)}</span>` : ""}</span><b>${got != null && got !== l.qty ? `${got}/` : ""}${l.qty}</b></div>`;
    })
    .join("")}<div><span>Total</span><b>${f1(o.m3)} m³ · ${kg(o.kg)} kg</b></div></div></div>`;
  if (o.proof) {
    const p = o.proof;
    h += `<div class="sec"><div class="eyebrow">Proof of delivery</div><div class="proofbox">${p.photo ? `<div class="pimg"><img src="${p.photo}" alt="Delivery photo"></div>` : ""}${p.signature ? `<div class="pimg" style="background:#fff"><img src="${p.signature}" alt="Receiver signature"></div>` : ""}</div><p class="meta">Received by ${esc(p.receiver || "store staff")} · recorded ${fmt(M(p.at))}${p.offline ? " offline" : ""}${p.synced_at ? " · synced" : ""}</p></div>`;
  }
  if (o.receipt) h += `<div class="sec"><div class="eyebrow">Store receipt</div><p class="meta" style="margin:0">${esc(o.receipt.by)} confirmed at ${fmt(M(o.receipt.at))} · ${esc(o.receipt.status)}</p></div>`;
  if (!o.deferred && !o.delivered && t && !["out", "done"].includes(t.status))
    h += `<div class="iacts"><button class="dbtn" data-act="moveask:${o.id}">${ic("arrowR")}Move…</button><button class="dbtn" data-act="deferask:${o.id}">${ic("cal")}Defer…</button><button class="dbtn" data-act="locate:${o.trip}">${ic("target")}Show trip</button></div>`;
  else if (!o.deferred && !o.delivered && t && isDark(t))
    h += `<div class="iacts"><button class="dbtn primary" data-act="moveask:${o.id}">${ic("arrowR")}Move while dark…</button><button class="dbtn" data-act="locate:${o.trip}">${ic("target")}Show trip</button></div>`;
  return h;
}
function tripInspector(t) {
  const v = S.vehicles[t.veh];
  const st = tripState(t);
  const budget = t.brand === "Fresh" ? 270 : 480;
  const used = t.brand === "Fresh" ? freshMinutes(v.id) : dayMinutes(v.id);
  let h = `<div class="eyebrow" style="color:var(--night-ink-3)">Trip ${t.id} · ${esc(DEPOTS[t.depot]?.name || "")}</div><h3>${v.id} · trip ${t.n}</h3><div class="meta">${v.temp === "reefer" ? "Refrigerated" : "Dry"} ${v.type} · ${t.brand} · ${esc(t.district)} · ${esc(t.driver)}${t.controlled ? " · live driver app" : ""}</div>
  <div class="row" style="margin-top:12px;flex-wrap:wrap"><span class="chip blue">${STATUS_LABEL[st] || st}</span><span class="chip">Departs ${fmt(t.departedAt ?? t.depart)}</span><span class="chip">Bay ${t.bay}${t.depot === "PEL" ? " · dock " + t.dock : ""}</span>${t.locked ? `<span class="chip">${ic("lock")} Locked</span>` : ""}</div>`;
  if (isDark(t)) h += darkCorridorHTML(t);
  else if (t.controlled) {
    const back = S.feed.find((f) => f.title.startsWith(`${t.veh} back online`));
    if (back) h += `<div class="dc ok"><div class="dc-h">${ic("wifi")}<div><b>${esc(back.title)}</b><small>${back.body}</small></div></div></div>`;
  }
  if (t.releasedAt != null) h += `<p class="meta" style="margin-top:12px">Released ${fmt(t.releasedAt)}${t.seal ? ` · seal ${esc(t.seal)}` : ""}${t.tempC != null ? ` · ${t.tempC} °C` : ""}</p>`;
  else if (st === "loading" && t.loading) h += `<p class="meta" style="margin-top:12px">Loading at the dock: ${t.loading.done} of ${t.loading.total} lines on the truck.</p>`;
  h += `<div class="sec"><div class="eyebrow">Capacity</div>${gauge("Volume", t.m3, v.m3, "m³")}${gauge("Weight", t.kg, v.kg, "kg")}${gauge(t.brand === "Fresh" ? "Fresh window, all trips" : "Daytime window", used, budget, "min")}${gauge("Fuel this week", v.used + (t.litres || 0), v.quota, "L")}</div>
  <div class="sec"><div class="eyebrow">Stops, in delivery order</div><div class="istops">${tripOrders(t)
    .map(
      (o, i) =>
        `<div class="s"><span class="q ${o.delivered ? "done" : ""}">${i + 1}</span><div><b>${esc(S.outlets[o.outlet].name)}</b><div class="meta" style="margin:0">${o.temp === "chilled" ? "Chilled" : "Ambient"} · ${f1(o.m3)} m³${o.latePlanned ? " · after window" : ""}</div></div><div class="r">${o.delivered ? "✓ " + fmt(o.deliveredAt) : "~" + fmt(o.eta)}<br><span style="color:${o.risk > 0.45 ? "var(--t-ember)" : "var(--t-green)"}">${o.delivered ? "" : Math.round(o.risk * 100) + "% risk"}</span></div></div>`,
    )
    .join("")}</div></div>
  <div class="iacts"><button class="dbtn" data-act="lock:${t.id}">${ic("lock")}${t.locked ? "Unlock trip" : "Lock trip"}</button></div>`;
  return h;
}
function darkCorridorHTML(t) {
  const pos = vehiclePos(t);
  const os = tripOrders(t);
  const done = os.filter((o) => o.delivered);
  const est = Math.min(pos.idx + 1, os.length);
  const queued = os.filter((o) => o.pendingMove).length;
  const left = os.filter((o) => !o.delivered && !o.pendingMove);
  return `<div class="dc"><div class="dc-h">${ic("wifiOff")}<div><b>Dark Corridor · no signal${nowMin() - (t.darkSince ?? nowMin()) >= 1 ? ` for ${dur(nowMin() - t.darkSince)}` : ` since ${fmt(t.darkSince)}`}</b><small>Last contact ${fmt(t.darkSince)}${done.length ? " after " + esc(S.outlets[done[done.length - 1].outlet].name) : ""}</small></div></div>
  <div class="dc-g"><div><small>Known</small><b>${done.length} of ${os.length} stops synced</b></div><div><small>Estimated now</small><b>Stop ${est} · ${esc(S.outlets[os[est - 1]?.outlet]?.name ?? "")}</b></div><div><small>Stores see</small><b>Estimated arrivals</b></div><div><small>Your changes</small><b>${queued ? queued + " queued for sync" : "Wait for sync"}</b></div></div>
  <p>The driver’s phone keeps recording offline. When it reconnects, a recorded delivery beats any plan change made while it was dark.</p>
  ${left.length ? `<div class="iacts">${left
    .slice(-2)
    .map((o) => `<button class="dbtn ${o === left[left.length - 1] ? "primary" : ""}" data-act="moveask:${o.id}">${ic("arrowR")}Move ${esc(S.outlets[o.outlet].name)}…</button>`)
    .join("")}</div>` : ""}</div>`;
}

/* ------------------------------------------------------------------ modals */

export function openDeferModal(o) {
  const ou = S.outlets[o.outlet];
  openModal(`<h3>Defer ${esc(ou.name)} to tomorrow?</h3><p>${o.brand} · ${o.temp} · ${f1(o.m3)} m³ · ${kg(o.kg)} kg. The store is told straight away, and the outlet moves up the priority list for the next run.</p>
  <div class="eyebrow" style="margin-top:18px;color:var(--night-ink-3)">Reason for the record</div>
  <div class="reasons">${["Refrigerated space", "Delivery window", "Store asked to move it", "Access problem"].map((r) => `<button data-act="deferwith:${o.id}|${r}">${r}</button>`).join("")}</div>
  <div class="iacts" style="margin-top:20px"><button class="dbtn" data-act="closemodal">Keep it on the plan</button></div>`);
}

export async function openMoveModal(o) {
  const ou = S.outlets[o.outlet];
  const t = S.trips[o.trip];
  const dark = t && isDark(t);
  openModal(`<div class="eyebrow" style="color:${dark ? "var(--t-ember)" : "var(--t-blue)"}">${dark ? `Dark Corridor · ${t.veh} has no signal` : "Move an order"}</div><h3>${dark ? `Move ${esc(ou.name)} while ${esc((t.driver || "").split(" ")[0])} can’t hear you?` : `Where can ${esc(ou.name)} go?`}</h3>
  <p>${dark ? "The phone keeps working offline but only learns about the move when it reconnects. If the stop is delivered by then, the delivery stands and the move is cancelled." : "Every option is checked against capacity, refrigeration, access, delivery windows, the trip budget and fuel."}</p>
  <div class="mopts"><div class="lv-loading">${ic("sync", "spin")}Checking every vehicle at ${esc(DEPOTS[o.depot]?.name || "")}…</div></div>
  <div class="iacts" style="margin-top:16px"><button class="dbtn" data-act="closemodal">Cancel</button></div>`);
  let list = [];
  try {
    CV.opts[o.id] = null;
    list = await moveOptions(o);
  } catch (e) {
    toast(esc(String(e.message || e)), "ember", "alert");
  }
  const box = document.querySelector("#modal .mopts");
  if (!box) return;
  if (!list.length) {
    box.innerHTML = `<p class="meta">No vehicle at this depot can take it today.</p>`;
    return;
  }
  const firstOk = list.findIndex((x) => x.ok);
  box.innerHTML = list
    .map(
      (x, i) =>
        `<button class="opt ${i === firstOk ? "rec" : ""}" ${x.ok ? `data-act="move:${o.id}|${x.vehicle}|${x.trip_no}"` : "disabled"}>${ic(x.ok ? (dark ? "clock" : "arrowR") : "ban")}<div>${i === firstOk ? `<span class="tag">${dark ? "Queue until sync" : "Best fit"}</span>` : ""}<b>${x.vehicle} · trip ${x.trip_no}${x.existing ? "" : " (new trip)"}</b>${esc(x.message)}</div><span class="right">${esc((x.driver || "").split(" ")[0])}${x.departs ? "<br>departs " + fmt(M(x.departs)) : ""}</span></button>`,
    )
    .join("");
}

export async function openShortfallModal(issueId) {
  const i = S.issues.find((x) => String(x.id) === String(issueId));
  let opts = [];
  try {
    opts = (await hooks.api.get(`/api/dispatch/issues/${issueId}/options`)).options;
  } catch (e) {
    toast(esc(String(e.message || e)), "ember", "alert");
    return;
  }
  const o = i && Object.values(S.orders).find((x) => x.dbId === i.order);
  const t = o && S.trips[o.trip];
  const ou = o && S.outlets[o.outlet];
  const kind = i ? i.kind.replace("_at_dock", "").replace("short", "missing").replace("wrong", "wrong item") : "";
  const title = i && i.kind === "driver_report" ? `Driver report · ${t ? t.veh : ""}` : `Short at the dock: ${ou ? esc(ou.name) : ""}`;
  const body =
    i && i.kind === "driver_report"
      ? `${esc(i.raised_by)} reported ${esc(i.payload?.kind || "a problem")}${i.note ? `: ${esc(i.note)}` : ""}.`
      : i
        ? `${esc(i.raised_by)} flagged ${i.qty} × ${esc(o ? lineName(o, i.line) : "items")} ${kind} on ${t ? t.veh : "the truck"}${o && t ? ` (stop ${t.stops.indexOf(o.id) + 1})` : ""}. Pick what happens before it leaves. The store and the driver are told either way.`
        : "";
  const ICON = { partial: "send", hold: "clock", next: "cal", ack: "check" };
  openModal(`<h3>${title}</h3><p>${body}</p>${i && i.photo ? `<div class="proofbox" style="margin-top:12px"><div class="pimg"><img src="${i.photo}" alt="Photo from the dock"></div></div>` : ""}
  ${opts.map((x) => `<button class="opt ${x.recommended ? "rec" : ""}" data-act="decide:${issueId}|${x.key}">${ic(ICON[x.key] || "check")}<div>${x.recommended ? `<span class="tag">Recommended</span>` : ""}<b>${esc(x.title)}</b>${esc(x.body)}</div></button>`).join("")}`);
}

export function closeAll() {
  closeModal();
  hideHover();
}
