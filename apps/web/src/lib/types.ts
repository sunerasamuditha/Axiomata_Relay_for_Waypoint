/** API shapes (kept in step with apps/api/relay_api/domain/views.py). */

export type Role = "dispatcher" | "loader" | "driver" | "store";

export interface Me {
  id: number;
  email: string;
  name: string;
  short_name: string;
  initials: string;
  color: string;
  role: Role;
  title: string;
  depot: string | null;
  dock: number | null;
  vehicle_id: string | null;
  outlet_id: string | null;
  lang: string;
  home: string;
  workspace: { id: string; code: string; name: string; sandbox: boolean };
}

export interface ClockPayload {
  now: string;
  anchor_virtual: string;
  anchor_real: string;
  rate: number;
  paused: boolean;
  service_date: string;
  cutoff: string;
  orders_closed_at: string | null;
  workspace: string;
  workspace_code: string;
  sandbox: boolean;
}

export interface Notice {
  id: number;
  kind: "blue" | "ember" | "green" | "amber";
  icon: string;
  title: string;
  body: string;
  at: string;
  actions: [string, string][];
  entity: string | null;
  key?: string | null;
  read: boolean;
}

export interface Flag {
  issue_id: number;
  kind: string;
  qty: number;
  status: "open" | "decided" | "resolved";
  decision: string | null;
  decision_text: string | null;
  decided_by: string | null;
  raised_by: string;
  raised_at: string;
}

export interface Line {
  id: number;
  sku: string;
  name: string;
  qty: number;
  uom: string;
  load_state: "todo" | "loaded" | "short" | "damaged";
  loaded_qty: number | null;
  loaded_by: string | null;
  loaded_at: string | null;
  delivered_qty: number | null;
  received_qty: number | null;
  receipt_issue: string | null;
  flag: Flag | null;
}

export interface Proof {
  receiver: string;
  photo: string | null;
  signature: string | null;
  at: string;
  note: string;
  simulated: boolean;
  offline: boolean;
  synced_at: string | null;
}

/* dock */
export interface DockTripRow {
  id: number;
  code: string;
  vehicle: string;
  type: string;
  temp: string;
  cap_kg: number;
  cap_m3: number;
  trip_no: number;
  brand: string;
  district: string;
  n_stops: number;
  depart: string;
  status: string;
  bay: number;
  dock: number;
  lines_total: number;
  lines_done: number;
  flags_open: number;
  crew: string | null;
  driver: string;
  controlled: string;
  has_chilled: boolean;
}

export interface DockQueue {
  clock: ClockPayload;
  depot: string;
  dock: number;
  plan: { version: number; published_at: string; policy: string } | null;
  pending_plan: boolean;
  trips: DockTripRow[];
  changes: Notice[];
  crew_roster: Crew[];
  dispatcher: string;
  depot_name: string;
}

export interface Crew {
  name: string;
  short: string;
  initials: string;
  color: string;
  bay: number;
}

export interface DockVisit {
  stop_id: number;
  seq: number;
  order_id: number;
  ref: string;
  outlet: string;
  name: string;
  temp: string;
  units: number;
  m3: number;
  kg: number;
  window: [string, string];
  dock_type: string;
  eta: string;
  manager: string;
  lines: Line[];
}

export interface Issue {
  id: number;
  kind: string;
  severity: string;
  status: string;
  trip: number | null;
  stop: number | null;
  order: number | null;
  line: number | null;
  qty: number | null;
  note: string;
  raised_by: string;
  raised_role: string;
  raised_at: string;
  decision: string | null;
  decision_text: string | null;
  decided_by: string | null;
  decided_at: string | null;
  photo: string | null;
  payload: Record<string, unknown>;
}

export interface DockTrip {
  clock: ClockPayload;
  dispatcher: string;
  trip: {
    id: number;
    code: string;
    vehicle: string;
    type: string;
    temp: string;
    cap_kg: number;
    cap_m3: number;
    trip_no: number;
    brand: string;
    district: string;
    status: string;
    depart: string;
    bay: number;
    dock: number;
    driver: string;
    released_at: string | null;
    released_by: string | null;
    seal: string | null;
    temp_c: number | null;
    doors_ok: boolean | null;
    m3: number;
    kg: number;
    needs_temp: boolean;
  };
  load_order: DockVisit[];
  progress: { total: number; done: number; waiting: number; m3: number; kg: number };
  issues: Issue[];
}

/* driver */
export interface RunOrder {
  order_id: number;
  ref: string;
  temp: string;
  units: number;
  m3: number;
  lines: Line[];
}

export interface Visit {
  visit_id: number;
  stop_ids: number[];
  seq: number;
  outlet: string;
  name: string;
  district: string;
  dock_type: string;
  parking: string;
  window: [string, string];
  eta: string;
  eta_kind: "actual" | "predicted" | "estimate";
  band_lo?: string;
  band_hi?: string;
  planned_arrival: string;
  late_prob: number;
  status: "pending" | "arrived" | "delivered" | "partial" | "failed";
  orders: RunOrder[];
  contact: string;
  arrived_at: string | null;
  completed_at: string | null;
  proof: Proof | null;
}

export interface RunTrip {
  id: number;
  code: string;
  trip_no: number;
  status: string;
  dock: number;
  bay: number;
  lines_total: number;
  lines_done: number;
  loader: string | null;
  brand: string;
  district: string;
  depot: string;
  depart: string;
  return: string | null;
  released_at: string | null;
  released_by: string | null;
  seal: string | null;
  temp_c: number | null;
  departed_at: string | null;
  signal: string;
  visits: Visit[];
  km: number;
  minutes: number;
}

export interface Run {
  clock: ClockPayload;
  vehicle: { id: string; type: string; temp: string; kg: number; m3: number; depot: string } | null;
  driver: { name: string; short: string; lang: string };
  dispatcher: string;
  depot_name: string;
  plan: { version: number; published_at: string } | null;
  pending_plan: boolean;
  trips: RunTrip[];
  notices: Notice[];
  known_issues: Issue[];
}

/* store */
export interface Step {
  key: string;
  label: string;
  state: "" | "done" | "now" | "warn";
}

export interface StoreOrder {
  id: number;
  ref: string;
  temp: string;
  brand: string;
  units: number;
  m3: number;
  kg: number;
  service_date: string;
  status: string;
  steps: Step[];
  channel: string;
  placed_at: string;
  window: [string, string];
  note: string;
  eta: string | null;
  eta_kind: string | null;
  band_lo: string | null;
  band_hi: string | null;
  vehicle: string | null;
  driver: string | null;
  trip_status: string | null;
  signal: string | null;
  dark_since: string | null;
  visit: number | null;
  visits: number | null;
  visits_done: number | null;
  departed_at: string | null;
  arrived_at: string | null;
  delivered_at: string | null;
  late_prob: number | null;
  proof: Proof | null;
  deferral: { code: string; text: string; streak: number; next_date: string | null } | null;
  carried_from: string | null;
  deferred_yesterday: boolean;
  receipt: { at: string; by: string; status: string; note: string } | null;
  shortfalls: { qty: number; line: string; decision_text: string | null; status: string }[];
  lines: Line[];
  events?: { type: string; at: string; actor: string }[];
}

export interface StoreHome {
  clock: ClockPayload;
  outlet: { id: string; name: string; brand: string; district: string; depot: string; dock_type: string; parking: string; window: [string, string] };
  manager: { name: string; short: string };
  focus_date: string;
  deliveries: StoreOrder[];
  previous_date: string;
  previous: StoreOrder[];
  upcoming: { date: string; orders: StoreOrder[] }[];
  ordering: { date: string; cutoff: string; open: boolean };
  notices: Notice[];
}

export interface Catalog {
  service_date: string;
  cutoff: string;
  window: [string, string];
  products: { sku: string; name: string; temp: string; uom: string; unit_kg: number; unit_m3: number; qty: number }[];
  standing: Record<string, Record<string, number>>;
  existing: { id: number; ref: string; temp: string; status: string; units: number }[];
}
