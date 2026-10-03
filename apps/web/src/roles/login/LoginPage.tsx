import { useState } from "react";
import { errorText, post } from "../../lib/api";
import type { Me } from "../../lib/types";
import { Icon } from "../../ui/Icon";
import "../../ui/ds.css";
import "./login.css";

const DEMO = [
  { email: "nirosha@waypoint.lk", name: "Nirosha Perera", role: "Dispatcher", where: "Peliyagoda planning office", icon: "layers", device: "Desktop" },
  { email: "kasun@waypoint.lk", name: "Kasun Bandara", role: "Loader", where: "Kandy Hub · Dock 1", icon: "box", device: "Tablet or phone" },
  { email: "sunil@waypoint.lk", name: "Sunil Rathnayake", role: "Driver", where: "VEH057 · reefer van", icon: "truck", device: "Phone" },
  { email: "fathima@waypoint.lk", name: "Fathima Rizwan", role: "Store manager", where: "Nuwara Eliya Town", icon: "store", device: "Desktop or phone" },
];

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState(() => localStorage.getItem("relay.ws") ?? "");
  const [showCode, setShowCode] = useState(() => !!localStorage.getItem("relay.ws"));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [made, setMade] = useState<string | null>(null);

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const me = await post<Me>("/api/auth/login", { email, password, workspace: code.trim() || null });
      try {
        if (code.trim()) localStorage.setItem("relay.ws", code.trim().toUpperCase());
        else localStorage.removeItem("relay.ws");
      } catch {
        /* ignore */
      }
      const next = new URLSearchParams(location.search).get("next");
      window.location.assign(next && next.startsWith(me.home) ? next : me.home);
    } catch (ex) {
      setErr(errorText(ex));
      setBusy(false);
    }
  };

  const sandbox = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await post<{ code: string }>("/api/demo/sandboxes");
      setCode(r.code);
      setShowCode(true);
      setMade(r.code);
    } catch (ex) {
      setErr(errorText(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <section className="login-art" aria-hidden="true">
        <div className="la-top">
          <img src="/logo.png" alt="" width={44} height={44} />
          <div>
            <b>Relay</b>
            <small>Waypoint Group · delivery planning</small>
          </div>
        </div>
        <div className="la-mid">
          <h1>
            One plan,
            <br />
            four faces.
          </h1>
          <p>
            The dispatcher plans under real limits. The dock loads in stop order. The driver records every stop, even out of coverage. The store sees it arrive.
          </p>
          <div className="faces">
            {DEMO.map((d) => (
              <div key={d.role} className="face">
                <span className="fi">
                  <Icon name={d.icon} />
                </span>
                <div>
                  <b>{d.role}</b>
                  <small>{d.device}</small>
                </div>
              </div>
            ))}
          </div>
        </div>
        <p className="la-foot">Tech-Triathlon 2026 · fictional company, synthetic data</p>
      </section>

      <section className="login-side">
        <form className="login-card" onSubmit={submit}>
          <div className="lc-head">
            <img className="lc-logo" src="/logo.png" alt="" width={40} height={40} />
            <div>
              <h2>Sign in</h2>
              <p className="muted">Your account decides what you see.</p>
            </div>
          </div>
          <label className="flabel" htmlFor="email">
            Email
          </label>
          <input id="email" className="input text" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <label className="flabel" htmlFor="pw" style={{ marginTop: 14 }}>
            Password
          </label>
          <input
            id="pw"
            className="input text"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {showCode ? (
            <>
              <label className="flabel" htmlFor="code" style={{ marginTop: 14 }}>
                Demo workspace code <span className="muted">(blank = shared demo)</span>
              </label>
              <input id="code" className="input" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="RLY-7K2Q" />
            </>
          ) : null}
          {err ? (
            <p className="err" role="alert" style={{ marginTop: 12 }}>
              {err}
            </p>
          ) : null}
          {made ? (
            <div className="card blue" style={{ marginTop: 14 }}>
              <b>Private sandbox {made} is ready.</b>
              <p className="sm" style={{ marginTop: 4 }}>
                Sign in to each role with this code. Nobody else sees your changes.
              </p>
            </div>
          ) : null}
          <button type="submit" className="btn primary lg block" style={{ marginTop: 18 }} disabled={busy || !email || !password}>
            {busy ? "Signing in…" : "Sign in"}
            <Icon name="arrowR" />
          </button>
          {!showCode ? (
            <button type="button" className="linkbtn" onClick={() => setShowCode(true)}>
              I have a demo workspace code
            </button>
          ) : null}

          <div className="demo-acc">
            <div className="eyebrow">Demo accounts · password relay2026</div>
            {DEMO.map((d) => (
              <button
                type="button"
                key={d.email}
                className={`acc ${email === d.email ? "on" : ""}`}
                onClick={() => {
                  setEmail(d.email);
                  setPassword("relay2026");
                }}
              >
                <span className="fi">
                  <Icon name={d.icon} />
                </span>
                <div>
                  <b>
                    {d.name} <em>{d.role}</em>
                  </b>
                  <small>
                    {d.email} · {d.where}
                  </small>
                </div>
              </button>
            ))}
            <button type="button" className="btn ghost block" style={{ marginTop: 10 }} onClick={sandbox} disabled={busy}>
              <Icon name="shield" /> Start a private sandbox
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
