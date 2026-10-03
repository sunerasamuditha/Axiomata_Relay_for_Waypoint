/**
 * Offline-first writes for the driver and the dock.
 *
 *  record(type, payload)  saves to IndexedDB first (with the device's virtual time), then tries to
 *                         send; if there is no connection it stays queued
 *  flush()                sends queued records in order to POST /api/sync; the server applies each
 *                         once (client_event_id), reports duplicates and rejections, and returns
 *                         anything that changed while the device was dark
 *
 * "Test offline mode" (Me tab) simulates a dead zone without touching the phone's radio: requests
 * are not sent and heartbeats stop, so the dispatcher sees the van go dark for real.
 */
import { useLiveQuery } from "dexie-react-hooks";
import { useEffect, useSyncExternalStore } from "react";
import { NetworkError, post } from "../api";
import { setClock, snapTo, toNaive } from "../clock";
import { uid } from "../format";
import type { ClockPayload } from "../types";
import { db, type OutboxItem } from "./db";

const SIM_KEY = "relay.simulateOffline";
let simulated = (() => {
  try {
    return localStorage.getItem(SIM_KEY) === "1";
  } catch {
    return false;
  }
})();
const subs = new Set<() => void>();
const notify = () => subs.forEach((f) => f());

export function isOnline(): boolean {
  return navigator.onLine && !simulated;
}

export function setSimulatedOffline(v: boolean) {
  simulated = v;
  try {
    localStorage.setItem(SIM_KEY, v ? "1" : "0");
  } catch {
    /* ignore */
  }
  notify();
  if (!v) void flush();
}

export function useConnectivity() {
  const state = useSyncExternalStore(
    (f) => {
      subs.add(f);
      window.addEventListener("online", f);
      window.addEventListener("offline", f);
      return () => {
        subs.delete(f);
        window.removeEventListener("online", f);
        window.removeEventListener("offline", f);
      };
    },
    () => `${navigator.onLine ? 1 : 0}${simulated ? 1 : 0}`,
  );
  return { online: state === "10", simulated: state.endsWith("1"), radio: state.startsWith("1") };
}

export function useOutbox(): OutboxItem[] {
  return useLiveQuery(() => db.outbox.orderBy("createdAt").reverse().limit(80).toArray(), [], []) ?? [];
}

export function usePending(): number {
  return useLiveQuery(() => db.outbox.where("status").equals("pending").count(), [], 0) ?? 0;
}

type Listener = (r: SyncResponse) => void;
const syncListeners = new Set<Listener>();
export function onSynced(fn: Listener) {
  syncListeners.add(fn);
  return () => {
    syncListeners.delete(fn);
  };
}

export interface SyncResponse {
  results: { client_event_id: string; status: "applied" | "duplicate" | "rejected"; message?: string }[];
  changes: string[];
  clock: ClockPayload;
}

/** Save a record locally (always), then try to send it. `at` snaps the local clock forward. */
export async function record(type: string, payload: Record<string, unknown>, label: string, atMs?: number | null): Promise<OutboxItem> {
  const when = snapTo(atMs ?? null);
  const item: OutboxItem = {
    id: uid(),
    type,
    payload,
    at: toNaive(when),
    offline: !isOnline(),
    status: "pending",
    createdAt: Date.now(),
    attempts: 0,
    label,
  };
  await db.outbox.add(item);
  void flush();
  return item;
}

let flushing: Promise<void> | null = null;

export function flush(): Promise<void> {
  if (flushing) return flushing;
  const run = async () => {
    while (isOnline()) {
      const batch = await db.outbox.where("status").equals("pending").sortBy("createdAt");
      if (!batch.length) break;
      const chunk = batch.slice(0, 40);
      let res: SyncResponse;
      try {
        res = await post<SyncResponse>("/api/sync", {
          device_id: deviceId(),
          // the server applies dispatcher changes queued while the phone was dark only after the
          // phone's own records are in, so a delivery made offline always wins
          pending_after: batch.length - chunk.length,
          events: chunk.map((i) => ({ client_event_id: i.id, type: i.type, payload: i.payload, at: i.at, offline: i.offline })),
        });
      } catch (e) {
        // no connection, or the server refused the batch (e.g. the session expired): keep the
        // records and try again on the next kick; nothing recorded on the phone is ever dropped
        await Promise.all(chunk.map((i) => db.outbox.update(i.id, { attempts: i.attempts + 1 })));
        if (!(e instanceof NetworkError)) console.warn("sync failed", e);
        break;
      }
      const now = Date.now();
      await db.transaction("rw", db.outbox, async () => {
        for (const r of res.results) {
          await db.outbox.update(r.client_event_id, {
            status: r.status === "rejected" ? "rejected" : "synced",
            syncedAt: now,
            syncedVirtual: res.clock?.now,
            message: r.message,
          });
        }
      });
      setClock(res.clock);
      syncListeners.forEach((f) => f(res));
    }
  };
  // reset in a later microtask: when there is nothing to send, run() settles synchronously
  flushing = run().finally(() => {
    flushing = null;
  });
  return flushing;
}

function deviceId(): string {
  try {
    let id = localStorage.getItem("relay.device");
    if (!id) {
      id = uid().slice(0, 12);
      localStorage.setItem("relay.device", id);
    }
    return id;
  } catch {
    return "browser";
  }
}

/** Keep trying: on reconnect, every 10 s, and when the tab regains focus. Heartbeats for drivers. */
export function useSyncLoop(heartbeat: boolean) {
  useEffect(() => {
    const kick = () => void flush();
    window.addEventListener("online", kick);
    document.addEventListener("visibilitychange", kick);
    const id = setInterval(kick, 10_000);
    let hb: ReturnType<typeof setInterval> | null = null;
    if (heartbeat) {
      const beat = async () => {
        if (!isOnline()) return;
        try {
          const pending = await db.outbox.where("status").equals("pending").count();
          const r = await post<{ clock: ClockPayload; changes: string[] }>("/api/driver/heartbeat", { pending });
          setClock(r.clock);
          if (r.changes?.length) syncListeners.forEach((f) => f({ results: [], changes: r.changes, clock: r.clock }));
        } catch {
          /* next beat */
        }
      };
      void beat();
      hb = setInterval(beat, 15_000);
    }
    kick();
    return () => {
      window.removeEventListener("online", kick);
      document.removeEventListener("visibilitychange", kick);
      clearInterval(id);
      if (hb) clearInterval(hb);
    };
  }, [heartbeat]);
}

export async function clearSynced() {
  await db.outbox.where("status").anyOf("synced", "rejected").delete();
}
