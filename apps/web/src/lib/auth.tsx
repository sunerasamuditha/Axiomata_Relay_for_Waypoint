import { useQuery } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { ApiError, get, NetworkError, post } from "./api";
import type { Me, Role } from "./types";

const ME_KEY = "relay.me";

function cachedMe(): Me | undefined {
  try {
    const raw = localStorage.getItem(ME_KEY);
    return raw ? (JSON.parse(raw) as Me) : undefined;
  } catch {
    return undefined;
  }
}

async function fetchMe(): Promise<Me> {
  try {
    const me = await get<Me>("/api/auth/me");
    try {
      localStorage.setItem(ME_KEY, JSON.stringify(me));
    } catch {
      /* private mode: fine */
    }
    return me;
  } catch (e) {
    // A reload in a dead zone: keep working as the last signed-in person on this device. The
    // server still checks the session on every record when the phone syncs.
    const offline = e instanceof NetworkError ? cachedMe() : undefined;
    if (offline) return offline;
    if (e instanceof ApiError && e.status === 401) forgetMe();
    throw e;
  }
}

function forgetMe() {
  try {
    localStorage.removeItem(ME_KEY);
  } catch {
    /* ignore */
  }
}

export function useMe() {
  return useQuery<Me>({
    queryKey: ["me"],
    queryFn: fetchMe,
    retry: (n, e) => !(e instanceof ApiError && e.status === 401) && n < 2,
    staleTime: 5 * 60_000,
  });
}

export async function signOut() {
  forgetMe();
  try {
    await post("/api/auth/logout");
  } finally {
    // full reload: each role loads only its own code and styles
    window.location.assign("/login");
  }
}

function Splash() {
  return (
    <div className="splash" style={{ position: "fixed", inset: 0, display: "grid", placeItems: "center", background: "#F3F4F7" }}>
      <img src="/logo.png" alt="" width={56} height={56} style={{ borderRadius: "50%" }} />
    </div>
  );
}

/** Only the account's own role may enter; everyone else is sent to their own home. */
export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const me = useMe();
  const unauth = me.error instanceof ApiError && me.error.status === 401;
  useEffect(() => {
    if (unauth) window.location.replace("/login?next=" + encodeURIComponent(location.pathname));
    else if (me.data && me.data.role !== role) window.location.replace(me.data.home);
  }, [unauth, me.data, role]);
  if (me.data?.role === role) return <>{children}</>;
  if (me.error && !unauth) {
    return (
      <div
        className="splash"
        style={{
          position: "fixed",
          inset: 0,
          display: "grid",
          placeItems: "center",
          background: "#F3F4F7",
          fontFamily: "Archivo, Arial, sans-serif",
          padding: 24,
          textAlign: "center",
        }}
      >
        <p>Relay can't reach the server. Check the connection and reload.</p>
      </div>
    );
  }
  return <Splash />;
}
