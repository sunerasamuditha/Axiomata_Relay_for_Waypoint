import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes, useNavigate, useParams } from "react-router";
import { errorText, get, post } from "../../lib/api";
import { signOut, useMe } from "../../lib/auth";
import { parseNaive, setClock, useVirtualNow } from "../../lib/clock";
import { dayLabel, dur, hm, minutesBetween, weekday } from "../../lib/format";
import { useLive, useLiveStatus } from "../../lib/live";
import type { Catalog, Notice, StoreHome, StoreOrder, StoreProfile } from "../../lib/types";
import { Icon } from "../../ui/Icon";
import { Logo, RelaySteps, ToastProvider, useToast, WindowBar } from "../../ui/kit";
import "../../ui/ds.css";
import "./store.css";

function useHome() {
  const live = useLiveStatus();
  const q = useQuery<StoreHome>({ queryKey: ["store", "home"], queryFn: () => get("/api/store/home"), refetchInterval: live ? false : 20_000 });
  useEffect(() => {
    if (q.data) setClock(q.data.clock);
  }, [q.data]);
  return q;
}

export default function StoreApp() {
  useLive([["store"]]);
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}

type Tab = { to: string; icon: string; label: string; end?: boolean; badge?: number };

function Shell() {
  const me = useMe().data!;
  const home = useHome();
  const unread = home.data?.notices.filter((n) => !n.read).length ?? 0;
  const tabs: Tab[] = [
    { to: "/store", end: true, icon: "home", label: "Today" },
    { to: "/store/order", icon: "plus", label: "Order" },
    { to: "/store/track", icon: "route", label: "Track" },
    { to: "/store/alerts", icon: "bell", label: "Alerts", badge: unread },
  ];
  // phones have no sidebar: Profile (delivery PIN, sign out) gets its own tab
  const phoneTabs: Tab[] = [...tabs, { to: "/store/me", icon: "user", label: "Me" }];
  return (
    <div className="st-app">
      <aside className="st-side">
        <div className="st-brand">
          <Logo size={36} />
          <div>
            <b>Relay</b>
            <small>Waypoint {home.data?.outlet.brand ?? "Fresh"}</small>
          </div>
        </div>
        <nav>
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end} className={({ isActive }) => (isActive ? "on" : "")}>
              <Icon name={t.icon} />
              {t.label}
              {t.badge ? <span className="badge">{t.badge}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="st-me">
          <NavLink to="/store/me" className={({ isActive }) => `st-me-link ${isActive ? "on" : ""}`} title="Profile and delivery PIN">
            <span className="av" style={{ background: me.color }}>
              {me.initials}
            </span>
            <div>
              <b>{me.name}</b>
              <small>{home.data?.outlet.name ?? ""}</small>
            </div>
          </NavLink>
          <button className="xbtn" onClick={signOut} aria-label="Sign out" title="Sign out">
            <Icon name="logout" />
          </button>
        </div>
      </aside>
      <main className="st-main">
        {home.isLoading ? <div className="skeleton" style={{ height: 320, margin: 24 }} /> : null}
        {home.error ? (
          <div className="empty">
            <Icon name="alert" />
            <b>Can't load your deliveries</b>
            <span>{errorText(home.error)}</span>
          </div>
        ) : null}
        {home.data ? (
          <Routes>
            <Route index element={<Today h={home.data} />} />
            <Route path="order" element={<Order />} />
            <Route path="track" element={<Track h={home.data} />} />
            <Route path="track/:id" element={<Track h={home.data} />} />
            <Route path="receipt/:id" element={<Receipt h={home.data} />} />
            <Route path="alerts" element={<Alerts h={home.data} />} />
            <Route path="me" element={<Profile />} />
          </Routes>
        ) : null}
      </main>
      <nav className="st-tabs" aria-label="Store">
        {phoneTabs.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end} className={({ isActive }) => (isActive ? "on" : "")}>
            <span className="ti">
              <Icon name={t.icon} />
              {t.badge ? <span className="badge">{t.badge}</span> : null}
            </span>
            {t.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

/* ------------------------------------------------------------------ today */

function greeting(ms: number) {
  const h = new Date(ms).getUTCHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function Today({ h }: { h: StoreHome }) {
  const now = useVirtualNow(15_000);
  const nav = useNavigate();
  const left = minutesBetween(now, h.ordering.cutoff);
  const latest = h.notices.slice(0, 4);
  return (
    <div className="st-page">
      <header className="st-hero">
        <div className="eyebrow">
          Waypoint {h.outlet.brand} · {h.outlet.id}
        </div>
        <h1>
          {greeting(now)}, {h.manager.short}
        </h1>
        <p className="muted">
          {h.outlet.name} · {dayLabel(now)} · {hm(now)}
        </p>
      </header>
      <div className="st-grid">
        <section>
          <div className="eyebrow sec">Deliveries for {dayLabel(h.focus_date, true)}</div>
          {h.deliveries.length === 0 ? (
            <div className="card empty">
              <Icon name="box" />
              <b>No orders for this day yet</b>
              <button className="btn primary" onClick={() => nav("/store/order")}>
                <Icon name="plus" /> Place an order
              </button>
            </div>
          ) : (
            h.deliveries.map((o) => <DeliveryCard key={o.id} o={o} now={now} />)
          )}
          {h.previous.length ? (
            <>
              <div className="eyebrow sec">Earlier · {dayLabel(h.previous_date, true)}</div>
              {h.previous.map((o) => (
                <DeliveryCard key={o.id} o={o} now={now} compact />
              ))}
            </>
          ) : null}
        </section>
        <aside className="st-col">
          <div className={`cutoff ${h.ordering.open ? "" : "closed"}`}>
            <Icon name="clock" />
            <div>
              <small>
                {weekday(h.ordering.date)}'s order closes at 4:00 PM{" "}
                {weekday(h.ordering.cutoff, false) !== weekday(now, false) ? `${weekday(h.ordering.cutoff, false)}` : "today"}
              </small>
              <b>{h.ordering.open ? `${dur(left)} left` : "Closed"}</b>
            </div>
            <button className="btn primary" onClick={() => nav("/store/order")} disabled={!h.ordering.open}>
              Order
            </button>
          </div>
          {h.upcoming.map((u) => (
            <div key={u.date} className="card blue placed">
              <div className="eyebrow" style={{ color: "var(--blue-7)" }}>
                Placed for {weekday(u.date)}
              </div>
              {u.orders.map((o) => (
                <div key={o.id} className="spread prow">
                  <span className="mono">{o.ref}</span>
                  <span className="chip green">
                    <Icon name="check" /> {o.status === "placed" ? "Received" : "Confirmed"} {hm(o.placed_at)}
                  </span>
                </div>
              ))}
            </div>
          ))}
          <div className="card latest">
            <div className="spread">
              <h3>Latest</h3>
              <button className="chip" onClick={() => nav("/store/alerts")}>
                All
              </button>
            </div>
            {latest.map((n) => (
              <NoticeRow key={n.id} n={n} />
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

const STATUS_CHIP: Record<string, [string, string]> = {
  placed: ["Ordered", ""],
  confirmed: ["Waiting for the plan", ""],
  planned: ["Planned", "blue"],
  loading: ["Loading", "blue"],
  loaded: ["Loaded", "blue"],
  out: ["On the road", "blue"],
  arrived: ["At your door", "blue"],
  delivered: ["Delivered", "green"],
  partial: ["Delivered, partial", "amber"],
  failed: ["Not delivered", "ember"],
  received: ["Received", "green"],
  deferred: ["Moved to tomorrow", "ember"],
};

function DeliveryCard({ o, now, compact }: { o: StoreOrder; now: number; compact?: boolean }) {
  const nav = useNavigate();
  const [label, tone] = STATUS_CHIP[o.status] ?? [o.status, ""];
  const done = ["delivered", "partial", "received", "failed"].includes(o.status);
  const dark = o.signal === "dark" && o.status === "out";
  return (
    <article className={`card dcard ${compact ? "compact" : ""}`}>
      <div className="spread">
        <span className={`chip ${tone}`}>{label}</span>
        <span className="muted sm">
          {o.temp === "chilled" ? "Chilled" : "Ambient"} · {o.units} cases <span className="mono">{o.ref}</span>
        </span>
      </div>
      {!compact ? (
        <>
          <div className="spread big">
            <div>
              <div className="eyebrow">
                {done ? "Arrived" : o.status === "deferred" ? "New day" : dark ? "Estimated" : o.band_lo ? "Expected" : "Arrival time"}
              </div>
              <div className="bt num">
                {done
                  ? hm(o.arrived_at ?? o.delivered_at)
                  : o.status === "deferred"
                    ? weekday(o.deferral?.next_date ?? o.service_date)
                    : o.band_lo
                      ? `${hm(o.band_lo)}–${hm(o.band_hi)}`
                      : "Set at 4 PM"}
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div className="eyebrow">Your window</div>
              <div className="wt num">
                {o.window[0]}–{o.window[1]}
              </div>
            </div>
          </div>
          {o.status !== "deferred" ? (
            <WindowBar
              window={o.window}
              day={o.service_date}
              bandLo={o.band_lo}
              bandHi={o.band_hi}
              arrived={o.arrived_at}
              now={now}
              risk={o.late_prob}
              estimate={dark}
            />
          ) : null}
          <RelaySteps steps={o.steps} estimate={dark} />
        </>
      ) : null}
      {o.carried_from && !done ? (
        <p className="note blue">
          <Icon name="sync" /> Carried over from yesterday. First in line on this run.
        </p>
      ) : null}
      {o.deferral ? (
        <p className="note ember">
          <Icon name="cal" /> {o.deferral.text.split(". ")[0]}.{" "}
          {o.deferral.streak > 1 ? "Second run in a row: you are first in line next time." : "It comes on the next run."}
        </p>
      ) : null}
      {dark ? (
        <p className="note ember">
          <Icon name="wifiOff" /> {o.vehicle} is out of coverage. The arrival is an estimate until it reconnects.
        </p>
      ) : null}
      {o.shortfalls.map((s, i) => (
        <p key={i} className="note ember">
          <Icon name="box" /> {s.qty} × {s.line} short. {s.decision_text ? "Following on the next run." : "Dispatch is deciding."}
        </p>
      ))}
      {o.proof ? (
        <p className="muted sm" style={{ marginTop: 10 }}>
          Delivered {hm(o.delivered_at)}
          {o.proof.pin_verified ? " · confirmed with your PIN" : o.proof.receiver ? ` · received by ${o.proof.receiver}` : ""}
          {o.proof.offline ? " · recorded offline" : ""}
        </p>
      ) : o.vehicle && !compact ? (
        <p className="muted sm" style={{ marginTop: 10 }}>
          {o.vehicle} with {o.driver?.split(" ")[0]}
          {o.visit ? ` · you are stop ${o.visit} of ${o.visits}` : ""}
        </p>
      ) : null}
      <div className="row acts">
        {o.status === "delivered" || o.status === "partial" ? (
          <button className="btn primary" onClick={() => nav(`/store/receipt/${o.id}`)}>
            <Icon name="check" /> Confirm receipt
          </button>
        ) : null}
        {o.receipt ? (
          <span className="chip green lg">
            <Icon name="check" /> You confirmed receipt
          </span>
        ) : null}
        {!compact ? (
          <button className="btn" onClick={() => nav(`/store/track/${o.id}`)}>
            Track <Icon name="chevR" />
          </button>
        ) : null}
      </div>
    </article>
  );
}

function NoticeRow({ n }: { n: Notice }) {
  return (
    <div className="nrow">
      <span className={`dot ${n.kind}`} />
      <div>
        <div className="spread">
          <b>{n.title}</b>
          <span className="muted sm num">{hm(n.at)}</span>
        </div>
        {n.body ? <p className="muted sm">{n.body}</p> : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ order */

type Placed = { ref: string; temp: string; units: number; service_date: string };

function Order() {
  const toast = useToast();
  const qc = useQueryClient();
  const now = useVirtualNow(15_000);
  const cat = useQuery<Catalog>({ queryKey: ["store", "catalog"], queryFn: () => get("/api/store/catalog") });
  const [qty, setQty] = useState<Record<string, number> | null>(null);
  const [busy, setBusy] = useState(false);
  // what was placed, frozen at that moment: the catalog moves on to the next day after the cutoff
  const [done, setDone] = useState<{ at: number; orders: Placed[] } | null>(null);
  useEffect(() => {
    if (cat.data && qty === null) setQty(Object.fromEntries(cat.data.products.map((p) => [p.sku, p.qty])));
  }, [cat.data, qty]);
  if (!cat.data || !qty) return <div className="skeleton" style={{ height: 420, margin: 24 }} />;
  const c = cat.data;
  const groups = [
    { temp: "chilled", title: "Chilled", sub: "needs a refrigerated vehicle", icon: "snow" },
    { temp: "ambient", title: "Ambient", sub: "", icon: "box" },
  ].filter((g) => c.products.some((p) => p.temp === g.temp));
  const totals = c.products.reduce(
    (a, p) => ({ units: a.units + (qty[p.sku] || 0), m3: a.m3 + (qty[p.sku] || 0) * p.unit_m3, kg: a.kg + (qty[p.sku] || 0) * p.unit_kg }),
    { units: 0, m3: 0, kg: 0 },
  );
  const left = minutesBetween(now, c.cutoff);
  const submit = async () => {
    setBusy(true);
    try {
      const r = await post<{ orders: Placed[] }>("/api/store/orders", { lines: qty });
      setDone({ at: now, orders: r.orders });
      toast("Order placed. Dispatch has it.", "green", "check");
      qc.invalidateQueries({ queryKey: ["store"] });
    } catch (e) {
      toast(errorText(e), "ember", "alert");
    } finally {
      setBusy(false);
    }
  };
  if (done) {
    const day = done.orders[0]?.service_date ?? c.service_date;
    const editable = c.service_date === day && left > 0;
    return (
      <div className="st-page narrow">
        <div className="done-hero" style={{ padding: "8px 0 0" }}>
          <div className="big">
            <Icon name="check" />
          </div>
          <h1>Order placed for {weekday(day)}</h1>
          <p>Dispatch plans after the 4:00 PM cutoff. Your arrival time appears on Today as soon as the plan is published.</p>
        </div>
        <div className="stack" style={{ marginTop: 22 }}>
          {done.orders.map((o) => (
            <div key={o.ref} className="card spread">
              <div>
                <b className="mono">{o.ref}</b>
                <p className="muted sm">
                  {o.temp === "chilled" ? "Chilled" : "Dry goods"} · {o.units} cases
                </p>
              </div>
              <span className="chip green">
                <Icon name="check" /> Received {hm(done.at)}
              </span>
            </div>
          ))}
          {editable ? (
            <button className="btn" onClick={() => setDone(null)}>
              Edit before the cutoff
            </button>
          ) : (
            <p className="muted sm">Orders for {weekday(day)} are closed. Dispatch is planning the day.</p>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="st-page narrow">
      <header className="ohead">
        <div>
          <div className="eyebrow">
            For {dayLabel(c.service_date)} · window {c.window[0]}–{c.window[1]}
          </div>
          <h1>New order</h1>
        </div>
        <span className={`chip ${left > 60 ? "blue" : "ember"} lg`}>
          <Icon name="clock" /> {left > 0 ? `${dur(left)} to cutoff` : "Cutoff passed"}
        </span>
      </header>
      {c.existing.length ? (
        <p className="note blue" style={{ marginBottom: 12 }}>
          <Icon name="info" /> You already have {c.existing.map((e) => e.ref).join(", ")} for this day. Placing again replaces it.
        </p>
      ) : (
        <p className="note blue" style={{ marginBottom: 12 }}>
          <Icon name="info" /> Your usual order is filled in. If you change nothing, it is placed automatically at the cutoff.
        </p>
      )}
      {groups.map((g) => (
        <section key={g.temp} className="card ocard">
          <div className="row ogh">
            <span className={g.temp === "chilled" ? "snow" : ""}>
              <Icon name={g.icon} />
            </span>
            <h3>{g.title}</h3>
            {g.sub ? <span className="muted sm">{g.sub}</span> : null}
          </div>
          {c.products
            .filter((p) => p.temp === g.temp)
            .map((p) => (
              <div key={p.sku} className="orow">
                <div>
                  <b>{p.name}</b>
                  <small className="muted">
                    {g.title} · {p.uom}
                  </small>
                </div>
                <div className="stepper" role="group" aria-label={p.name}>
                  <button type="button" aria-label={`Fewer ${p.name}`} onClick={() => setQty({ ...qty, [p.sku]: Math.max(0, (qty[p.sku] || 0) - 1) })}>
                    <Icon name="minus" />
                  </button>
                  <span>{qty[p.sku] || 0}</span>
                  <button type="button" aria-label={`More ${p.name}`} onClick={() => setQty({ ...qty, [p.sku]: (qty[p.sku] || 0) + 1 })}>
                    <Icon name="plus" />
                  </button>
                </div>
              </div>
            ))}
        </section>
      ))}
      <div className="card soft totals">
        <div>
          <b className="num">{totals.units}</b>
          <small>cases</small>
        </div>
        <div>
          <b className="num">{totals.m3.toFixed(1)}</b>
          <small>m³</small>
        </div>
        <div>
          <b className="num">{Math.round(totals.kg).toLocaleString()}</b>
          <small>kg</small>
        </div>
      </div>
      <button className="btn primary lg block" style={{ marginTop: 14 }} disabled={busy || totals.units === 0 || left <= 0} onClick={submit}>
        {busy ? "Placing…" : `Place order for ${weekday(c.service_date)}`}
      </button>
      <p className="muted sm" style={{ textAlign: "center", marginTop: 8 }}>
        Chilled and dry goods travel in different vehicles, so they become two orders.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ track */

function Track({ h }: { h: StoreHome }) {
  const { id } = useParams();
  const nav = useNavigate();
  const now = useVirtualNow(10_000);
  const all = [...h.deliveries, ...h.previous];
  const pick = id ? Number(id) : (all.find((o) => ["out", "arrived", "loaded", "loading", "planned"].includes(o.status)) ?? all[0])?.id;
  const q = useQuery<StoreOrder>({ queryKey: ["store", "order", pick], queryFn: () => get(`/api/store/orders/${pick}`), enabled: !!pick });
  const o = q.data;
  if (!pick)
    return (
      <div className="empty">
        <Icon name="route" />
        <b>Nothing to track yet</b>
      </div>
    );
  const dark = o?.signal === "dark" && o?.status === "out";
  return (
    <div className="st-page narrow">
      <div className="seg trackseg" role="tablist">
        {all.map((x) => (
          <button key={x.id} className={x.id === pick ? "on" : ""} onClick={() => nav(`/store/track/${x.id}`)} role="tab" aria-selected={x.id === pick}>
            {x.temp === "chilled" ? "Chilled" : "Dry"} · {weekday(x.service_date, false)}
          </button>
        ))}
      </div>
      {!o ? (
        <div className="skeleton" style={{ height: 300 }} />
      ) : (
        <>
          <section className="card track">
            <div className="spread">
              <div>
                <div className="eyebrow">
                  {o.temp === "chilled" ? "Chilled" : "Dry goods"} · <span className="mono">{o.ref}</span>
                </div>
                <h1 className="num">
                  {["delivered", "partial", "received"].includes(o.status)
                    ? `Delivered ${hm(o.delivered_at)}`
                    : o.status === "deferred"
                      ? "Moved to tomorrow"
                      : o.band_lo
                        ? `${dark ? "Estimated" : "Expected"} ${hm(o.band_lo)}–${hm(o.band_hi)}`
                        : "Waiting for the plan"}
                </h1>
              </div>
              {o.vehicle ? (
                <div className="vchip">
                  <Icon name={o.vehicle === "VEH057" ? "van" : "truck"} />
                  <div>
                    <b>{o.vehicle}</b>
                    <small>{o.driver}</small>
                  </div>
                </div>
              ) : null}
            </div>
            {o.status !== "deferred" ? (
              <WindowBar
                window={o.window}
                day={o.service_date}
                bandLo={o.band_lo}
                bandHi={o.band_hi}
                arrived={o.arrived_at}
                now={now}
                risk={o.late_prob}
                estimate={dark}
              />
            ) : null}
            <RelaySteps steps={o.steps} estimate={dark} />
            {o.visit && o.visits ? (
              <>
                <p className="route-cap">
                  Your store is stop {o.visit} of {o.visits} on {o.vehicle}'s run
                </p>
                <div className="route-line" aria-label={`Stop ${o.visit} of ${o.visits}`}>
                  {Array.from({ length: o.visits }, (_, i) => (
                    <span
                      key={i}
                      className={`rl ${i < (o.visits_done ?? 0) ? "done" : ""} ${i + 1 === o.visit ? "you" : ""} ${dark && i === (o.visits_done ?? 0) ? "est" : ""}`}
                      aria-current={i + 1 === o.visit ? "step" : undefined}
                    >
                      {i + 1 === o.visit ? (
                        <>
                          <Icon name="store" />
                          <span className="sr-only">{i + 1}</span>
                        </>
                      ) : (
                        i + 1
                      )}
                    </span>
                  ))}
                </div>
              </>
            ) : null}
            {dark ? (
              <p className="note ember">
                <Icon name="wifiOff" /> No signal from {o.vehicle} since {hm(o.dark_since)}. Its phone keeps recording; times update when it reconnects.
              </p>
            ) : null}
            {o.deferral ? <p className="note ember">{o.deferral.text}</p> : null}
          </section>
          {o.proof ? (
            <section className="card">
              <h3>Proof of delivery</h3>
              <div className="proofs">
                {o.proof.photo ? (
                  <img src={o.proof.photo} alt="Delivery photo" />
                ) : (
                  <div className="ph-none">
                    <Icon name="camera" /> No photo
                  </div>
                )}
              </div>
              {o.proof.pin_verified ? (
                <span className="chip green pinchip">
                  <Icon name="shield" /> Confirmed with your PIN
                </span>
              ) : null}
              <p className="muted sm">
                {o.proof.pin_verified ? "Recorded" : `Received by ${o.proof.receiver || "store staff"} · recorded`} {hm(o.proof.at)}
                {o.proof.offline ? " offline, synced when the van reconnected" : ""}
              </p>
              {o.status !== "received" ? (
                <button className="btn primary block" style={{ marginTop: 12 }} onClick={() => nav(`/store/receipt/${o.id}`)}>
                  <Icon name="check" /> Confirm what arrived
                </button>
              ) : null}
            </section>
          ) : null}
          <section className="card">
            <h3>Timeline</h3>
            <ol className="tl">
              {timeline(o).map((e, i) => (
                <li key={i}>
                  <b>{e.label}</b> <span className="muted">{e.when}</span>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </div>
  );
}

const EVENT_LABEL: Record<string, string> = {
  "order.moved": "Moved to another van",
  "order.deferred": "Moved to tomorrow",
  "order.split_requested": "Asked to split the order",
};

/** Everything that happened to an order, in time order (dates shown when they differ from the delivery day). */
function timeline(o: StoreOrder) {
  const day = o.service_date.slice(0, 10);
  const items: { label: string; at: string }[] = [];
  const add = (label: string, at: string | null | undefined) => {
    if (at) items.push({ label, at });
  };
  add(o.carried_from ? "Ordered (carried over)" : "Ordered", o.placed_at);
  (o.events ?? []).forEach((e) => EVENT_LABEL[e.type] && add(EVENT_LABEL[e.type], e.at));
  add("Left the depot", o.departed_at);
  add("Arrived", o.arrived_at);
  add(o.status === "partial" ? "Delivered, partial" : "Delivered", o.delivered_at);
  add("Receipt confirmed", o.receipt?.at);
  items.sort((a, b) => parseNaive(a.at) - parseNaive(b.at));
  return items.map((x) => ({ label: x.label, when: x.at.slice(0, 10) === day ? hm(x.at) : `${weekday(x.at, false)} ${hm(x.at)}` }));
}

/* ------------------------------------------------------------------ receipt */

function Receipt({ h }: { h: StoreHome }) {
  const { id } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const o = useMemo(() => [...h.deliveries, ...h.previous].find((x) => x.id === Number(id)), [h, id]);
  const [rows, setRows] = useState<Record<number, { qty: number; issue: string | null }>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (o) setRows(Object.fromEntries(o.lines.map((l) => [l.id, { qty: l.delivered_qty ?? l.qty, issue: null }])));
  }, [o]);
  if (!o)
    return (
      <div className="empty">
        <Icon name="receipt" />
        <b>Order not found</b>
      </div>
    );
  const submit = async () => {
    setBusy(true);
    try {
      await post(`/api/store/orders/${o.id}/receipt`, {
        lines: o.lines.map((l) => ({ line_id: l.id, received_qty: rows[l.id]?.qty ?? 0, issue: rows[l.id]?.issue })),
        note,
      });
      toast("Receipt confirmed. Dispatch has it.", "green", "check");
      qc.invalidateQueries({ queryKey: ["store"] });
      nav("/store");
    } catch (e) {
      toast(errorText(e), "ember", "alert");
    } finally {
      setBusy(false);
    }
  };
  const issues = Object.values(rows).filter((r) => r.issue).length;
  return (
    <div className="st-page narrow">
      <button className="backbtn" onClick={() => nav(-1)} aria-label="Back">
        <Icon name="chevL" />
      </button>
      <header className="ohead">
        <div>
          <div className="eyebrow">
            {o.temp === "chilled" ? "Chilled" : "Dry goods"} · <span className="mono">{o.ref}</span> · delivered {hm(o.delivered_at)}
          </div>
          <h1>Confirm what arrived</h1>
        </div>
      </header>
      <section className="card">
        {o.lines.map((l) => {
          const r = rows[l.id] ?? { qty: l.qty, issue: null };
          const sent = l.delivered_qty ?? l.qty;
          return (
            <div key={l.id} className="rrow">
              <div className="spread">
                <div>
                  <b>{l.name}</b>
                  <small className="muted">
                    {sent} {l.uom} sent{sent < l.qty ? ` (ordered ${l.qty})` : ""}
                  </small>
                </div>
                <div className="stepper">
                  <button type="button" aria-label="Fewer" onClick={() => setRows({ ...rows, [l.id]: { ...r, qty: Math.max(0, r.qty - 1) } })}>
                    <Icon name="minus" />
                  </button>
                  <span>{r.qty}</span>
                  <button type="button" aria-label="More" onClick={() => setRows({ ...rows, [l.id]: { ...r, qty: r.qty + 1 } })}>
                    <Icon name="plus" />
                  </button>
                </div>
              </div>
              <div className="issues">
                {["damaged", "missing", "wrong"].map((k) => (
                  <button
                    key={k}
                    type="button"
                    className={`nchip ${r.issue === k ? "on" : ""}`}
                    onClick={() => setRows({ ...rows, [l.id]: { ...r, issue: r.issue === k ? null : k } })}
                  >
                    {k === "wrong" ? "Wrong item" : k[0].toUpperCase() + k.slice(1)}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
        <label className="flabel" htmlFor="rnote" style={{ marginTop: 16 }}>
          Note for dispatch (optional)
        </label>
        <textarea
          id="rnote"
          className="input area"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. 1 case of yoghurt crushed"
        />
      </section>
      <button className="btn primary lg block" style={{ marginTop: 14 }} onClick={submit} disabled={busy}>
        <Icon name="check" /> {issues ? `Confirm and report ${issues} issue${issues > 1 ? "s" : ""}` : "Everything arrived"}
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ alerts */

function Alerts({ h }: { h: StoreHome }) {
  const qc = useQueryClient();
  useEffect(() => {
    if (h.notices.some((n) => !n.read)) void post("/api/store/notices/read").then(() => qc.invalidateQueries({ queryKey: ["store", "home"] }));
  }, [h.notices, qc]);
  return (
    <div className="st-page narrow">
      <header className="ohead">
        <h1>Alerts</h1>
      </header>
      <section className="card">
        {h.notices.length === 0 ? <p className="muted">Nothing yet.</p> : null}
        {h.notices.map((n) => (
          <NoticeRow key={n.id} n={n} />
        ))}
      </section>
      <p className="muted sm" style={{ textAlign: "center", marginTop: 10 }}>
        Times are Waypoint time ({hm(Date.UTC(2026, 0, 1) + (parseNaive(h.clock.now) % 86_400_000))} now).
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ profile */

function Profile() {
  const me = useMe().data!;
  // the PIN is never persisted: gcTime 0 drops it from memory as soon as this page closes
  const q = useQuery<StoreProfile>({ queryKey: ["store", "profile"], queryFn: () => get("/api/store/profile"), gcTime: 0 });
  const [show, setShow] = useState(false);
  const p = q.data;
  return (
    <div className="st-page narrow">
      <header className="ohead">
        <h1>Profile</h1>
      </header>
      {q.error ? (
        <div className="empty">
          <Icon name="alert" />
          <b>Can't load your profile</b>
          <span>{errorText(q.error)}</span>
        </div>
      ) : !p ? (
        <div className="skeleton" style={{ height: 280 }} />
      ) : (
        <div className="stack">
          <section className="card">
            <div className="row me-who">
              <span className="av lg" style={{ background: me.color }}>
                {me.initials}
              </span>
              <div>
                <h3>{p.name}</h3>
                <p className="muted sm">{p.title}</p>
              </div>
            </div>
            <dl className="me-facts">
              <div>
                <dt>Outlet</dt>
                <dd>
                  {p.outlet.name} <span className="mono muted">{p.outlet.id}</span>
                </dd>
              </div>
              <div>
                <dt>Email</dt>
                <dd>{p.email}</dd>
              </div>
            </dl>
          </section>
          <section className="card">
            <div className="spread">
              <h3>Delivery PIN</h3>
              {p.delivery_pin ? (
                <button className="chip" onClick={() => setShow(!show)} aria-pressed={show}>
                  <Icon name={show ? "eyeOff" : "eye"} /> {show ? "Hide" : "Show"}
                </button>
              ) : null}
            </div>
            {p.delivery_pin ? (
              <div className="pin-value num">{show ? p.delivery_pin : "•".repeat(p.delivery_pin.length)}</div>
            ) : (
              <p className="pin-none">No delivery PIN is set for your account yet.</p>
            )}
            <p className="muted sm">
              When Relay delivers, the driver hands you their phone. Type this PIN to confirm the delivery reached you. Keep it to yourself: drivers never see
              it.
            </p>
          </section>
          <button className="btn lg block" onClick={signOut}>
            <Icon name="logout" /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
