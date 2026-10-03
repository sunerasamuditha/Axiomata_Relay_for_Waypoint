/**
 * Dispatcher face. The desk itself is the approved prototype engine (./engine, vanilla JS, keeps
 * its own DOM); this wrapper feeds it the reference data and live snapshots and keeps it in sync
 * with the server (SSE invalidations → refetch → engine.update).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, errorText, get, post } from "../../lib/api";
import { signOut, useMe } from "../../lib/auth";
import { setClock } from "../../lib/clock";
import { useLive, useLiveStatus } from "../../lib/live";
import { mountDispatcher } from "./engine/index.js";
import "./dispatch.css";

type Engine = ReturnType<typeof mountDispatcher>;

export default function DispatchPage() {
  const me = useMe().data!;
  const qc = useQueryClient();
  useLive([["dispatch"]]);
  const live = useLiveStatus();
  const ref = useQuery({ queryKey: ["reference"], queryFn: () => get<Record<string, unknown>>("/api/reference"), staleTime: Infinity });
  const snap = useQuery({
    queryKey: ["dispatch", "snapshot"],
    queryFn: () => get<{ clock: Parameters<typeof setClock>[0] }>("/api/dispatch/snapshot"),
    refetchInterval: live ? 30_000 : 8_000,
  });
  const host = useRef<HTMLDivElement>(null);
  const eng = useRef<Engine | null>(null);
  const ready = !!ref.data && !!snap.data;

  useEffect(() => {
    if (snap.data) setClock(snap.data.clock);
  }, [snap.data]);

  useEffect(() => {
    if (!ready || !host.current) return;
    eng.current = mountDispatcher(host.current, {
      reference: ref.data,
      snapshot: snap.data,
      me,
      api: { get, post, patch: (path: string, body: unknown) => api(path, { method: "PATCH", body }) },
      signOut,
      refetch: () => qc.refetchQueries({ queryKey: ["dispatch", "snapshot"] }),
    });
    return () => {
      eng.current?.destroy();
      eng.current = null;
    };
    // mount once when the first data arrives; later snapshots go through update()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  useEffect(() => {
    if (snap.data && eng.current) eng.current.update(snap.data);
  }, [snap.data]);

  useEffect(() => {
    const h = () => {
      qc.invalidateQueries({ queryKey: ["dispatch"] });
    };
    window.addEventListener("relay:reset", h);
    return () => window.removeEventListener("relay:reset", h);
  }, [qc]);

  return (
    <>
      <div id="dispatch-app" ref={host} />
      {!ready ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            display: "grid",
            placeItems: "center",
            background: "#060A1A",
            color: "#9AA6D1",
            fontFamily: "Archivo, Arial, sans-serif",
            textAlign: "center",
            padding: 24,
          }}
        >
          {ref.error || snap.error ? (
            <div>
              <p style={{ fontSize: 18, color: "#fff", margin: 0 }}>The dispatch desk can’t reach the server.</p>
              <p style={{ marginTop: 8 }}>{errorText(ref.error || snap.error)}</p>
            </div>
          ) : (
            <img src="/logo.png" alt="" width={56} height={56} style={{ borderRadius: "50%", animation: "pulseLogo 1.4s ease-in-out infinite" }} />
          )}
        </div>
      ) : null}
    </>
  );
}
