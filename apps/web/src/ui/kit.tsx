/* Shared components for the loader, driver and store faces (one design system). */
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { parseNaive } from "../lib/clock";
import { hhmmToMin } from "../lib/format";
import type { Step } from "../lib/types";
import { Icon } from "./Icon";

/* ---------------------------------------------------------------- toasts */
type Toast = { id: number; text: string; kind?: string; icon?: string };
const ToastCtx = createContext<(text: string, kind?: string, icon?: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind = "", icon = "info") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-2), { id, text, kind, icon }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <Icon name={t.icon || "info"} />
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/* ---------------------------------------------------------------- logo */
export function Logo({ size = 34 }: { size?: number }) {
  return <img className="logo-mark" src="/logo.png" alt="Waypoint" width={size} height={size} style={{ width: size, height: size }} />;
}

/* ---------------------------------------------------------------- swipe to confirm */
export function Swipe({
  label,
  onDone,
  locked,
  lockedLabel,
  tone,
}: {
  label: string;
  onDone: () => void;
  locked?: boolean;
  lockedLabel?: string;
  tone?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [x, setX] = useState(0);
  const drag = useRef<{ sx: number; max: number } | null>(null);
  const reset = () => setX(0);
  return (
    <div
      ref={ref}
      className={`swipe ${locked ? "locked" : ""} ${tone ?? ""}`}
      role="button"
      tabIndex={0}
      aria-disabled={locked}
      aria-label={locked ? (lockedLabel ?? label) : label}
      onKeyDown={(e) => {
        if (!locked && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onDone();
        }
      }}
      onPointerDown={(e) => {
        if (locked || !ref.current) return;
        const r = ref.current.getBoundingClientRect();
        drag.current = { sx: e.clientX, max: r.width - 62 };
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        setX(Math.max(0, Math.min(drag.current.max, e.clientX - drag.current.sx)));
      }}
      onPointerUp={() => {
        if (!drag.current) return;
        const done = x > drag.current.max * 0.82;
        drag.current = null;
        if (done) {
          setX(9999);
          setTimeout(() => {
            onDone();
            reset();
          }, 120);
        } else reset();
      }}
      onPointerCancel={() => {
        drag.current = null;
        reset();
      }}
    >
      <div className="fillbar" style={{ width: Math.min(x + 56, 2000) }} />
      <div className="knob" style={{ transform: `translateX(${Math.min(x, ref.current ? ref.current.clientWidth - 62 : 0)}px)` }}>
        <Icon name={locked ? "lock" : "arrowR"} />
      </div>
      <span>{locked ? (lockedLabel ?? label) : label}</span>
    </div>
  );
}

/* ---------------------------------------------------------------- window bar (the signature component) */
export function WindowBar({
  window: [open, close],
  day,
  bandLo,
  bandHi,
  arrived,
  now,
  risk,
  estimate,
}: {
  window: [string, string];
  day: string;
  bandLo?: string | null;
  bandHi?: string | null;
  arrived?: string | null;
  now?: number;
  risk?: number | null;
  estimate?: boolean;
}) {
  const base = parseNaive(day.slice(0, 10));
  const m = (iso?: string | null) => (iso ? (parseNaive(iso) - base) / 60000 : null);
  const o = hhmmToMin(open);
  const c = hhmmToMin(close);
  const lo0 = m(bandLo);
  const hi0 = m(bandHi);
  const a = m(arrived);
  const n0 = now !== undefined ? (now - base) / 60000 : null;
  // the "now" marker only when it is near the window (the evening before, it would squash the bar)
  const n = n0 !== null && n0 >= hhmmToMin(open) - 240 && n0 <= hhmmToMin(close) + 240 ? n0 : null;
  const pts = [o, c, lo0, hi0, a, n].filter((v): v is number => v !== null && Number.isFinite(v));
  const lo = Math.min(...pts) - 25;
  const hi = Math.max(...pts) + 25;
  const span = Math.max(60, hi - lo);
  const X = (v: number) => Math.max(0, Math.min(100, ((v - lo) / span) * 100));
  const fmt = (v: number) =>
    `${String(Math.floor((((v % 1440) + 1440) % 1440) / 60)).padStart(2, "0")}:${String(Math.round(((v % 60) + 60) % 60)).padStart(2, "0")}`;
  return (
    <div className="wbar" aria-label={`Window ${open} to ${close}`}>
      <div className="track" />
      <div className="win" style={{ left: `${X(o)}%`, width: `${X(c) - X(o)}%` }} />
      {lo0 !== null && hi0 !== null && a === null ? (
        <div
          className={`band ${(risk ?? 0) > 0.45 ? "risk" : ""} ${estimate ? "est" : ""}`}
          style={{ left: `${X(lo0)}%`, width: `${Math.max(2, X(hi0) - X(lo0))}%` }}
        />
      ) : null}
      {a !== null ? <div className={`act ${a > c ? "late" : ""}`} style={{ left: `${X(a)}%` }} /> : null}
      {n !== null && n >= lo && n <= hi ? <div className="now" style={{ left: `${X(n)}%` }} /> : null}
      <div className="lbl" style={{ left: `${X(o)}%` }}>
        {fmt(o)}
      </div>
      <div className="lbl" style={{ left: `${X(c)}%` }}>
        {fmt(c)}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- relay steps */
export function RelaySteps({ steps, estimate }: { steps: Step[]; estimate?: boolean }) {
  return (
    <div className="relay">
      {steps.map((s) => (
        <div key={s.key} className={`st ${s.state} ${estimate && s.state === "now" ? "est" : ""}`}>
          <i />
          {s.label}
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- photo capture (compressed) */
export async function fileToJpeg(file: File, max = 1024, quality = 0.72): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = rej;
      i.src = url;
    });
    const s = Math.min(1, max / Math.max(img.width, img.height));
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * s);
    c.height = Math.round(img.height * s);
    c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", quality);
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function PhotoInput({ value, onChange, label, need }: { value: string | null; onChange: (v: string | null) => void; label: string; need?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <button type="button" className={`photo ${value ? "has" : ""} ${need && !value ? "need" : ""}`} onClick={() => ref.current?.click()}>
      {value ? <img src={value} alt="" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} /> : null}
      {value ? (
        <span className="ptag">
          <Icon name="camera" /> Retake
        </span>
      ) : (
        <>
          <Icon name="camera" />
          {label}
        </>
      )}
      <input
        ref={ref}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (f) onChange(await fileToJpeg(f));
          e.target.value = "";
        }}
      />
    </button>
  );
}

/* ---------------------------------------------------------------- signature pad */
export function SignaturePad({ onChange, label }: { onChange: (dataUrl: string | null) => void; label: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const dirty = useRef(false);
  useEffect(() => {
    const c = ref.current!;
    const r = c.getBoundingClientRect();
    c.width = r.width * 2;
    c.height = r.height * 2;
    const g = c.getContext("2d")!;
    g.scale(2, 2);
    g.lineWidth = 2.4;
    g.lineCap = "round";
    g.strokeStyle = "#0B0D12";
  }, []);
  const pos = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  };
  return (
    <div>
      <canvas
        ref={ref}
        className="sigpad"
        aria-label={label}
        onPointerDown={(e) => {
          drawing.current = true;
          const [x, y] = pos(e);
          const g = ref.current!.getContext("2d")!;
          g.beginPath();
          g.moveTo(x, y);
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!drawing.current) return;
          const [x, y] = pos(e);
          const g = ref.current!.getContext("2d")!;
          g.lineTo(x, y);
          g.stroke();
          dirty.current = true;
        }}
        onPointerUp={() => {
          drawing.current = false;
          if (dirty.current) onChange(ref.current!.toDataURL("image/png"));
        }}
      />
      <button
        type="button"
        className="chip outline"
        style={{ marginTop: 8 }}
        onClick={() => {
          const c = ref.current!;
          c.getContext("2d")!.clearRect(0, 0, c.width, c.height);
          dirty.current = false;
          onChange(null);
        }}
      >
        <Icon name="reset" /> Clear
      </button>
    </div>
  );
}

/* ---------------------------------------------------------------- overlays */
export function Drawer({
  title,
  sub,
  onClose,
  children,
  foot,
}: {
  title: string;
  sub?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  foot?: ReactNode;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="dh spread" style={{ alignItems: "flex-start" }}>
          <div>
            <h2>{title}</h2>
            {sub ? <p className="muted">{sub}</p> : null}
          </div>
          <button className="xbtn" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </div>
        <div className="db">{children}</div>
        {foot ? <div className="df">{foot}</div> : null}
      </aside>
    </>
  );
}

export function Meter({ label, a, b, unit, hiAt = 0.92 }: { label: string; a: number; b: number; unit: string; hiAt?: number }) {
  const p = b ? a / b : 0;
  const fmt = (v: number) =>
    unit === "kg" ? Math.round(v).toLocaleString("en-US") : unit === "m³" ? (Math.round(v * 10) / 10).toFixed(1) : String(Math.round(v));
  return (
    <div className="mrow">
      <div className="spread">
        <span>{label}</span>
        <b>
          {fmt(a)} / {fmt(b)}
          {unit && unit !== "lines" ? ` ${unit}` : ""}
        </b>
      </div>
      <div className={`capbar ${p > hiAt ? "hi" : ""}`}>
        <i style={{ width: `${Math.min(100, p * 100)}%` }} />
      </div>
    </div>
  );
}
