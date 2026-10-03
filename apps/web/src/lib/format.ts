import { parseNaive } from "./clock";

const pad = (n: number) => String(n).padStart(2, "0");

/** "03:05" from a naive ISO string or ms. */
export function hm(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "--:--";
  const ms = typeof v === "number" ? v : parseNaive(v);
  if (!Number.isFinite(ms)) return "--:--";
  const d = new Date(ms);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAYS_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function dayLabel(v: string | number, long = false): string {
  const ms = typeof v === "number" ? v : parseNaive(v);
  const d = new Date(ms);
  return `${(long ? DAYS_LONG : DAYS)[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

export function weekday(v: string | number, long = true): string {
  const ms = typeof v === "number" ? v : parseNaive(v);
  return (long ? DAYS_LONG : DAYS)[new Date(ms).getUTCDay()];
}

/** "1 h 20 m" / "35 min" */
export function dur(minutes: number): string {
  const m = Math.max(0, Math.round(minutes));
  const h = Math.floor(m / 60);
  return h ? `${h}h ${pad(m % 60)}m` : `${m} min`;
}

export function minutesBetween(a: string | number, b: string | number): number {
  const ma = typeof a === "number" ? a : parseNaive(a);
  const mb = typeof b === "number" ? b : parseNaive(b);
  return (mb - ma) / 60000;
}

export const f1 = (n: number) => (Math.round(n * 10) / 10).toFixed(1);
export const kg = (n: number) => Math.round(n).toLocaleString("en-US");
export const pct = (n: number) => `${Math.round(n * 100)}%`;

/** minutes after midnight for "HH:MM" */
export function hhmmToMin(s: string): number {
  const [h, m] = s.split(":").map(Number);
  return h * 60 + m;
}

export function sameDayAt(dayIso: string, hhmm: string): number {
  return parseNaive(dayIso.slice(0, 10)) + hhmmToMin(hhmm) * 60000;
}

export function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID().replace(/-/g, "");
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
