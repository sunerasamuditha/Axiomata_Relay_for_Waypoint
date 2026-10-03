/**
 * The workspace's virtual clock, mirrored in the browser.
 *
 * The server sends its anchor (virtual time at a real instant, rate, paused). Virtual times are
 * naive wall-clock strings ("2026-09-30T03:05:00", Sri Lanka time); here they are handled as
 * UTC-based millisecond numbers so the browser's own time zone never shifts them.
 * Offline, the phone keeps its own copy and snaps it forward when the driver records something.
 */
import { useEffect, useState, useSyncExternalStore } from "react";
import type { ClockPayload } from "./types";

type State = { anchorVirtual: number; anchorReal: number; rate: number; paused: boolean; payload: ClockPayload | null };

let state: State = { anchorVirtual: Date.UTC(2026, 8, 29, 15, 20), anchorReal: Date.now(), rate: 1, paused: true, payload: null };
const listeners = new Set<() => void>();
const KEY = "relay.clock";

try {
  const saved = localStorage.getItem(KEY);
  if (saved) state = { ...state, ...JSON.parse(saved) };
} catch {
  /* storage unavailable */
}

export function parseNaive(s: string | null | undefined): number {
  if (!s) return NaN;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(s);
  if (!m) return NaN;
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] ?? 0), +(m[5] ?? 0), +(m[6] ?? 0));
}

export function toNaive(ms: number): string {
  return new Date(ms).toISOString().slice(0, 19);
}

function emit() {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...state, payload: null }));
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export function setClock(p: ClockPayload) {
  const anchorVirtual = parseNaive(p.anchor_virtual);
  const anchorReal = Date.parse(p.anchor_real);
  const next = { anchorVirtual, anchorReal, rate: p.rate || 1, paused: p.paused, payload: p };
  // never move backwards locally (an offline phone may already be ahead of the server)
  if (virtualNowFrom(next) < virtualNow() - 1000 && state.payload) {
    state = { ...state, payload: p };
  } else {
    state = next;
  }
  emit();
}

function virtualNowFrom(s: State, real = Date.now()): number {
  if (s.paused) return s.anchorVirtual;
  return s.anchorVirtual + Math.max(0, real - s.anchorReal) * s.rate;
}

export function virtualNow(): number {
  return virtualNowFrom(state);
}

/** Move the local clock forward (offline actions); returns the time to record. */
export function snapTo(ms: number | null | undefined): number {
  const now = virtualNow();
  if (ms && Number.isFinite(ms) && ms > now) {
    state = { ...state, anchorVirtual: ms, anchorReal: Date.now() };
    emit();
    return ms;
  }
  return now;
}

export function clockInfo() {
  return state;
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Re-renders every `tick` ms while the clock runs; returns virtual now (ms). */
export function useVirtualNow(tick = 1000): number {
  const s = useSyncExternalStore(subscribe, () => state);
  const [, force] = useState(0);
  useEffect(() => {
    if (s.paused) return;
    const id = setInterval(() => force((x) => x + 1), tick);
    return () => clearInterval(id);
  }, [s.paused, tick]);
  return virtualNowFrom(s);
}

export function useClockState() {
  return useSyncExternalStore(subscribe, () => state);
}
