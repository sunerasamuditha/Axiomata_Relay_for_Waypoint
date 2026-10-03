/**
 * Live updates: one EventSource per tab. The server only sends invalidation signals for the
 * topics this user cares about; we refetch the affected queries (REST stays the source of truth).
 * If the stream is down, the role's main query polls instead (see useLiveStatus / refetchInterval).
 */
import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";
import { setClock } from "./clock";
import type { ClockPayload } from "./types";

let connected = false;
const subs = new Set<() => void>();
const setConnected = (v: boolean) => {
  if (connected !== v) {
    connected = v;
    subs.forEach((f) => f());
  }
};

export function useLiveStatus() {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => connected,
  );
}

export function useLive(keys: QueryKey[], enabled = true) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled) {
      setConnected(false);
      return;
    }
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    const open = () => {
      if (closed) return;
      es = new EventSource("/api/stream", { withCredentials: true });
      es.addEventListener("hello", () => setConnected(true));
      es.addEventListener("invalidate", (e) => {
        setConnected(true);
        let kinds: string[] = [];
        try {
          kinds = JSON.parse((e as MessageEvent).data).kinds ?? [];
        } catch {
          /* ignore */
        }
        keys.forEach((k) => qc.invalidateQueries({ queryKey: k }));
        if (kinds.includes("reset")) window.dispatchEvent(new CustomEvent("relay:reset"));
      });
      es.addEventListener("clock", (e) => {
        setConnected(true);
        try {
          setClock(JSON.parse((e as MessageEvent).data) as ClockPayload);
        } catch {
          /* ignore */
        }
      });
      es.onerror = () => {
        setConnected(false);
        if (es && es.readyState === EventSource.CLOSED) {
          es.close();
          timer = setTimeout(open, 4000);
        }
      };
    };
    open();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      es?.close();
      setConnected(false);
    };
    // keys are static per role page
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qc, enabled]);
}
