/**
 * Dispatcher pages: the deferral lever (policy trade-offs solved by the engine), the order queue,
 * and the 10-week cold-capacity outlook (from the Datathon demand forecast).
 */
import { $, clamp, dayLabel, dur, esc, f1, fmt, ic, kg, M, nowMin, S, weekdayLong } from "./core";

/* ------------------------------------------------------------------ lever */

export const LEVER_LABEL = {
  throughput: { label: "Max throughput", sub: "Serve the most orders" },
  balanced: { label: "Balanced", sub: "Protect cheap repeats" },
  fairness: { label: "Fairness first", sub: "No outlet skipped twice" },
};
const LV_Y = { fairness: 70, balanced: 210, throughput: 350 };

function coldOut() {
  const pel = Object.values(S.vehicles).filter((v) => v.depot === "PEL" && v.temp === "reefer");
  const kdy = Object.values(S.vehicles).filter((v) => v.depot === "KDY" && v.temp === "reefer");
  const out = (xs) => xs.filter((v) => v.status === "workshop").length;
  return { pel: pel.length, pelOut: out(pel), kdy: kdy.length, kdyOut: out(kdy) };
}

export function leverPageHTML() {
  if (!S.plan)
    return `<div class="eyebrow" style="color:var(--t-ember)">Deferral lever</div><h1>Plan first, then pull the lever</h1><p class="sub">The lever compares the three deferral policies on today’s real orders. Close orders and plan on the canvas to start.</p>`;
  const L = S.lever;
  const cur = S.plan.policy;
  const k = S.leverPending || cur;
  const c = coldOut();
  const head = `<div class="eyebrow" style="color:var(--t-ember)">Deferral lever · Plan v${S.plan.version}${S.plan.status === "draft" ? " (draft)" : ""}</div><h1>Who waits when the cold fleet runs out?</h1>
  <p class="sub">Refrigerated capacity is today’s limit: ${c.pelOut} of ${c.pel} cold vehicles at Peliyagoda${c.kdyOut ? ` and ${c.kdyOut} of ${c.kdy} at Kandy` : ""} are in the workshop. Move the lever to compare policies the optimiser has solved on today’s orders, see the cost, then ${S.plan.status === "published" ? "republish" : "apply"}. Stores are told the moment you publish.</p>`;
  const knob = `<div class="panel lever"><div class="lv-wrap"><div class="lv-track" id="lvtrack"><div class="lv-slot"></div><div class="lv-knob" id="lvknob" role="slider" aria-label="Deferral policy" aria-valuetext="${LEVER_LABEL[k].label}" tabindex="0"></div></div>
      <div class="lv-stops">${["fairness", "balanced", "throughput"].map((m) => `<button class="lv-stop ${k === m ? "on" : ""}" style="top:${LV_Y[m]}px" data-act="lever:${m}">${LEVER_LABEL[m].label}${m === cur ? " ·  now" : ""}<small>${LEVER_LABEL[m].sub}</small></button>`).join("")}</div></div>
      <button class="btn primary block" data-act="leverapply" ${k === cur || !L ? "disabled" : ""}>${ic("sync")}${S.plan.status === "published" ? "Publish policy" : "Apply to the draft"}</button>
      <p class="sub" style="font-size:12.5px;text-align:center;margin:0">${k === cur ? (S.plan.status === "published" ? "This is the published policy." : "This is the draft’s policy.") : "Previewing. Nothing changes until you apply."}</p>
    </div>`;
  if (!L || S.leverFor !== `${S.plan.id}:${S.plan.version}`) {
    return `${head}<div class="lever-grid">${knob}<div class="panel" style="grid-column:span 2"><div class="lv-loading">${ic("sync", "spin")}<div><b style="color:inherit">${S.leverError ? "Couldn’t solve the alternatives" : "Solving the other two policies on today’s orders…"}</b><br><small>${S.leverError ? esc(S.leverError) : "The optimiser runs each policy with the same rules. About 5 seconds the first time; cached after that."}</small></div></div></div></div>`;
  }
  const P = L.policies;
  const pen = P[k];
  const base = P[cur];
  const kp = pen.kpis;
  const kc = base.kpis;
  const diff = (a, b, good) => {
    const d = b - a;
    if (!d) return "";
    return `<span class="delta ${d > 0 === good ? "up" : "down"}">${d > 0 ? "+" : ""}${Number.isInteger(d) ? d : f1(d)}</span>`;
  };
  const changes = (pen.changes || []).map((ch) =>
    ch.type === "served"
      ? `<div class="chg plus">${ic("plus")}<div><b>${esc(ch.name)}</b> is served on ${ch.vehicle} trip ${ch.trip_no}${ch.repeat ? ", ending a repeat skip" : ""}.</div></div>`
      : `<div class="chg minus">${ic("minus")}<div><b>${esc(ch.name)}</b> moves to tomorrow. ${esc(ch.text || "")}</div></div>`,
  );
  const defs = [...(pen.deferred || [])].sort((a, b) => b.streak - a.streak || (b.repeat ? 1 : 0) - (a.repeat ? 1 : 0));
  return `${head}
  <div class="lever-grid">${knob}
    <div class="panel">
      <div class="eyebrow" style="color:var(--night-ink-3)">Impact of “${LEVER_LABEL[k].label}”</div>
      <div class="bignums" style="margin-top:12px">
        <div><b>${kp.served}${diff(kc.served, kp.served, true)}</b><small>orders served of ${kp.orders}</small></div>
        <div><b>${kp.deferred}${diff(kc.deferred, kp.deferred, false)}</b><small>orders deferred</small></div>
        <div><b>${kp.repeat_skips}${diff(kc.repeat_skips, kp.repeat_skips, false)}</b><small>outlets skipped twice in a row</small></div>
        <div><b>${f1(kp.chilled_served_m3)}${diff(kc.chilled_served_m3, kp.chilled_served_m3, true)}</b><small>of ${f1(kp.chilled_m3)} m³ chilled served</small></div>
      </div>
      <div class="kpirow"><span>Trips <b>${kp.trips}</b></span><span>Late stops <b>${kp.late_stops}</b></span><span>Distance <b>${kg(kp.km)} km</b></span><span>Fuel <b>${kg(kp.litres)} L</b></span><span>Solved in <b>${((pen.ms || 0) / 1000).toFixed(1)} s</b></span></div>
      <div class="eyebrow" style="color:var(--night-ink-3);margin-top:20px">What changes if you apply</div>
      ${changes.length ? changes.join("") : `<p class="sub" style="font-size:13.5px">Nothing. ${k === cur ? "This is the current plan." : "The same orders are served either way."}</p>`}
    </div>
    <div class="panel">
      <div class="spread"><div class="eyebrow" style="color:var(--night-ink-3)">Deferred under this policy (${defs.length})</div><span class="chip">Stores notified on publish</span></div>
      <div style="margin-top:12px">${defs
        .map((d) => {
          const streak = d.streak || 1;
          return `<div class="dcard ${streak > 1 ? "rep" : ""}"><div class="spread"><h4>${esc(d.name)}</h4><span class="chip ${d.kind === "unavoidable" ? "" : "ember"}">${d.kind === "unavoidable" ? "Unavoidable" : "Choice"} · ${esc(d.code)}</span></div>
        <div class="row" style="margin-top:8px;font-size:12.5px;color:var(--night-ink-2)"><span class="streakrow">${[0, 1, 2, 3, 4].map((i) => `<i class="${i >= 5 - streak ? "skip" : "ok"}"></i>`).join("")}</span>${streak > 1 ? `<b style="color:var(--t-ember)">Skipped ${streak} runs in a row</b>` : "First skip"} · ${d.brand} · ${d.temp} · ${f1(d.m3)} m³</div>
        <p>${esc(d.text)}</p></div>`;
        })
        .join("")}</div>
    </div>
  </div>`;
}
export function placeKnob() {
  const kn = $("#lvknob");
  if (kn) kn.style.top = LV_Y[S.leverPending || (S.plan && S.plan.policy) || "balanced"] + "px";
}
/** Drag the lever knob (pointer) or step it with the arrow keys. Returns a cleanup function. */
export function bindLever(update) {
  const down = (e) => {
    const tr = e.target.closest("#lvtrack");
    if (!tr) return;
    const kn = $("#lvknob");
    kn.classList.add("drag");
    const move = (ev) => {
      const r = tr.getBoundingClientRect();
      kn.style.top = clamp(ev.clientY - r.top, LV_Y.fairness, LV_Y.throughput) + "px";
    };
    const up = (ev) => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      kn.classList.remove("drag");
      const r = tr.getBoundingClientRect();
      const y = ev.clientY - r.top;
      let best = "balanced";
      let bd = 1e9;
      Object.entries(LV_Y).forEach(([m, yy]) => {
        if (Math.abs(yy - y) < bd) {
          bd = Math.abs(yy - y);
          best = m;
        }
      });
      S.leverPending = best;
      update();
    };
    move(e);
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  };
  const key = (e) => {
    if (!e.target || e.target.id !== "lvknob") return;
    const ord = ["fairness", "balanced", "throughput"];
    let i = ord.indexOf(S.leverPending || S.plan?.policy);
    if (e.key === "ArrowUp") i--;
    else if (e.key === "ArrowDown") i++;
    else return;
    e.preventDefault();
    S.leverPending = ord[clamp(i, 0, 2)];
    update();
    $("#lvknob")?.focus();
  };
  document.addEventListener("pointerdown", down);
  document.addEventListener("keydown", key);
  return () => {
    document.removeEventListener("pointerdown", down);
    document.removeEventListener("keydown", key);
  };
}

/* ------------------------------------------------------------------ order queue */

const VIA = { app: "App", phone: "Phone", standing: "Usual order", carryover: "Carried over" };

export function ordersPageHTML() {
  const q = S.queue;
  if (!q) return `<h1>Order queue</h1>`;
  const rows = [...q.orders];
  const cutMin = M(q.cutoff);
  const left = cutMin - nowMin();
  const forToday = q.date === S.serviceDate;
  const ch = rows.filter((r) => r.temp === "chilled").reduce((s, r) => s + r.m3, 0);
  const canClose = forToday && S.phase === "ordering";
  return `<div class="spread" style="flex-wrap:wrap;align-items:flex-end"><div><div class="eyebrow" style="color:var(--t-blue)">Order queue · for ${dayLabel(q.date)}</div><h1>${rows.length} order${rows.length === 1 ? "" : "s"} so far, closing at 4:00 PM ${forToday ? "" : weekdayLong(q.cutoff)}</h1><p class="sub">Orders come in from the store app and from phone calls. At the cutoff, any store that has not ordered gets its usual order. Anything that can never fit a vehicle is flagged before planning starts.</p></div>
  <div class="row"><span class="chip blue" style="height:34px;font-size:14px">${ic("clock")} ${left > 0 ? `Closes in ${dur(left)}` : "Cutoff passed"}</span>${canClose ? `<button class="dbtn primary" data-act="plan">${ic("lock")}Close orders and plan</button>` : ""}</div></div>
  <div class="bignums" style="grid-template-columns:repeat(4,1fr);margin-top:22px">
    <div><b>${rows.length}</b><small>orders received</small></div><div><b>${f1(ch)}</b><small>m³ chilled requested</small></div><div><b>${rows.filter((r) => r.channel === "phone").length}</b><small>entered from phone calls</small></div><div><b>${rows.filter((r) => r.flag).length}</b><small>need attention</small></div></div>
  <div class="panel" style="margin-top:18px;padding:8px 8px 4px"><div class="tablewrap"><table class="dtable"><thead><tr><th>Order</th><th>Outlet</th><th>Brand</th><th>Temp</th><th class="num">Units</th><th class="num">m³</th><th class="num">kg</th><th>Placed</th><th>Via</th><th>Checks</th></tr></thead><tbody>
  ${
    rows.length
      ? rows
          .map(
            (r) =>
              `<tr class="${r.carry_over ? "new" : ""}"><td><span class="mono">${esc(r.ref)}</span></td><td><b>${esc(r.name)}</b><br><small style="color:var(--night-ink-3)">${esc(r.district)}</small></td><td>${r.brand}</td><td>${r.temp === "chilled" ? ic("snow") + " Chilled" : "Ambient"}</td><td class="num">${r.units}</td><td class="num">${f1(r.m3)}</td><td class="num">${kg(r.kg)}</td><td class="num">${fmt(M(r.placed_at))}</td><td>${VIA[r.channel] || esc(r.channel)}</td><td>${r.flag ? `<span class="chip amber">${esc(r.flag)}</span>` : r.carry_over ? '<span class="chip ember">Deferred yesterday</span>' : '<span class="chip green">OK</span>'}</td></tr>`,
          )
          .join("")
      : `<tr><td colspan="10" style="text-align:center;padding:28px">No orders yet for ${dayLabel(q.date)}.</td></tr>`
  }
  </tbody></table></div></div>`;
}

/* ------------------------------------------------------------------ outlook */

const FEST = (s) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export function outlookPageHTML() {
  const O = S.outlook;
  if (!O) return `<div class="eyebrow" style="color:var(--t-blue)">Capacity outlook</div><h1>Loading the forecast…</h1>`;
  if (O.error) return `<div class="eyebrow" style="color:var(--t-blue)">Capacity outlook</div><h1>No forecast yet</h1><p class="sub">${esc(O.error)}</p>`;
  const weeks = O.weeks;
  const W = 720;
  const H = 330;
  const L = 48;
  const R = 16;
  const T = 34;
  const B = 58;
  const iw = W - L - R;
  const ih = H - T - B;
  const top = Math.max(30, ...weeks.map((w) => Math.max(w.need, w.have)));
  const max = Math.ceil((top * 1.12) / 30) * 30;
  const bw = iw / weeks.length;
  const y = (v) => T + ih - (v / max) * ih;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Refrigerated vehicle days needed against available, next ${weeks.length} weeks" style="width:100%;height:auto;display:block">`;
  for (let v = 0; v <= max; v += max / 4) s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" style="stroke:${v ? "var(--ol-grid)" : "var(--ol-axis)"}"/><text x="${L - 8}" y="${y(v) + 4}" style="fill:var(--ol-text)" font-size="11" text-anchor="end">${Math.round(v)}</text>`;
  weeks.forEach((w, i) => {
    const x = L + i * bw + bw * 0.2;
    const wd = bw * 0.6;
    const short = w.short > 0;
    const hb = Math.max(0, w.need - w.short);
    const tag = w.festivals && w.festivals.length ? FEST(w.festivals[0]) : "";
    const byDepot = Object.entries(w.depots || {})
      .map(([n, d]) => `${n} ${d.need} needed, ${d.have} available`)
      .join("; ");
    s += `<g><title>Week of ${w.label}: ${w.need} refrigerated-vehicle days needed, ${w.have} available${short ? `, ${w.short} short at depot level` : ""}${tag ? ` · ${tag}` : ""}. ${byDepot}</title>`;
    s += `<path d="M${x},${y(0)} V${y(hb) + 4} Q${x},${y(hb)} ${x + 4},${y(hb)} H${x + wd - 4} Q${x + wd},${y(hb)} ${x + wd},${y(hb) + 4} V${y(0)} Z" style="fill:var(--ol-need)"/>`;
    if (short) s += `<path d="M${x},${y(hb) - 2} V${y(w.need) + 4} Q${x},${y(w.need)} ${x + 4},${y(w.need)} H${x + wd - 4} Q${x + wd},${y(w.need)} ${x + wd},${y(w.need) + 4} V${y(hb) - 2} Z" style="fill:var(--ol-short)"/>`;
    s += `<line x1="${x - 6}" x2="${x + wd + 6}" y1="${y(w.have)}" y2="${y(w.have)}" style="stroke:var(--ol-tick)" stroke-width="2.5" stroke-linecap="round"/>`;
    if (short) s += `<text x="${x + wd / 2}" y="${Math.min(y(w.need), y(w.have)) - 8}" style="fill:var(--ol-short-text)" font-size="11.5" font-weight="700" text-anchor="middle">−${w.short}</text>`;
    s += `<text x="${x + wd / 2}" y="${H - B + 18}" style="fill:var(--ol-text)" font-size="11" text-anchor="middle">${w.label}</text>`;
    if (tag) s += `<text x="${x + wd / 2}" y="${H - B + 34}" style="fill:var(--ol-short-text)" font-size="10.5" text-anchor="middle">${esc(tag)}</text>`;
    s += `<rect x="${L + i * bw}" y="${T}" width="${bw}" height="${ih}" fill="transparent"/></g>`;
  });
  s += `<text x="${L}" y="16" style="fill:var(--ol-text)" font-size="11.5">Refrigerated-vehicle days per week (both depots)</text></svg>`;
  const shortWeeks = weeks.filter((w) => w.short > 0);
  const worst = O.worst && O.worst.short > 0 ? O.worst : null;
  const shortDepots = [...new Set(shortWeeks.flatMap((w) => Object.entries(w.depots || {}).filter(([, d]) => d.short > 0).map(([n]) => n)))];
  const title = shortWeeks.length
    ? `Cold capacity falls short in ${shortWeeks.length} of the next ${weeks.length} weeks${shortDepots.length === 1 ? ` at ${shortDepots[0]}` : ""}${worst ? `, worst the week of ${worst.label}` : ""}`
    : `Cold capacity covers the next ${weeks.length} weeks`;
  const workshop = Object.values(S.vehicles).filter((v) => v.temp === "reefer" && v.status === "workshop");
  const cards = [];
  if (worst) {
    const d = Object.entries(worst.depots)
      .filter(([, x]) => x.short > 0)
      .map(([n, x]) => `${n} needs ${x.need} and has ${x.have}`)
      .join("; ");
    const spare = Object.entries(worst.depots)
      .filter(([, x]) => x.have > x.need)
      .map(([n, x]) => `${n} has ${x.have - x.need} spare`)
      .join(", ");
    cards.push(
      `<div class="dcard rep"><h4>Week of ${worst.label}: ${worst.short} refrigerated-vehicle days short</h4><p>${worst.festivals?.length ? `${FEST(worst.festivals[0])} lifts chilled demand. ` : ""}${d}${spare ? ` (${spare}, but its vans serve its own stores)` : ""}. ${workshop.length ? `Bring ${workshop
        .slice(0, 2)
        .map((v) => v.id)
        .join(" and ")} back from the workshop before then, or hire ${Math.ceil(worst.short / Math.max(1, worst.operating_days))} refrigerated truck${Math.ceil(worst.short / Math.max(1, worst.operating_days)) > 1 ? "s" : ""} for the week.` : "Book hire trucks for the week."}</p></div>`,
    );
  }
  const first = weeks[0];
  if (first !== worst) cards.push(`<div class="dcard ${first.short ? "rep" : ""}"><h4>Week of ${first.label}: ${first.short ? `${first.short} short` : "covered"}</h4><p>${first.need} refrigerated-vehicle days needed, ${first.have} available after workshop time. ${first.short ? "Today’s deferrals are this gap reaching the dock." : "No action needed."}</p></div>`);
  else cards.push(`<div class="dcard"><h4>This week is the gap reaching the dock</h4><p>Today’s cold deferrals at Peliyagoda are the same shortage, one day early. Each vehicle back from the workshop adds about ${worst.operating_days} vehicle-days a week.</p></div>`);
  shortWeeks
    .filter((w) => w !== worst && w !== first)
    .slice(0, 2)
    .forEach((w) => cards.push(`<div class="dcard"><h4>Week of ${w.label}: ${w.short} short</h4><p>${w.festivals?.length ? FEST(w.festivals[0]) + " week. " : ""}Plan a hire or a repair ahead of it.</p></div>`));
  cards.push(
    `<div class="dcard"><h4>How to read this</h4><p>Bars are forecast need; the tick line is what the fleet can supply once workshop time is subtracted. Forecast: ${esc(O.method || "")}. Backtest error ${O.backtest?.mape_pct ?? "–"}% MAPE overall (${Object.entries(O.backtest?.mape_pct_by_brand || {})
      .map(([b, v]) => `${b} ${v}%`)
      .join(", ")}). One vehicle-day carries about ${f1(O.per_vehicle_day.Kandy)} m³ chilled from Kandy and ${f1(O.per_vehicle_day.Peliyagoda)} m³ from Peliyagoda.</p></div>`,
  );
  return `<div class="eyebrow" style="color:var(--t-blue)">Capacity outlook · next ${weeks.length} weeks</div><h1>${title}</h1><p class="sub">Weekly demand forecasts turned into refrigerated-vehicle days, set against the vehicles that will be out of the workshop. Plan hires and repairs before the gap reaches the dock.</p>
  <div class="outlook-grid"><div class="panel">${s}<div class="wlegend" style="margin-top:10px"><span><i style="background:var(--ol-need)"></i>Needed, covered</span><span><i style="background:var(--ol-short)"></i>Not covered at its own depot</span><span><i style="background:var(--ol-tick);height:3px"></i>Available</span></div></div>
  <div class="stack">${cards.join("")}</div></div>`;
}
