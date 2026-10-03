import { lazy, Suspense, useEffect } from "react";
import { Route, Routes } from "react-router";
import { RequireRole, useMe } from "./lib/auth";

const Login = lazy(() => import("./roles/login/LoginPage"));
const Dispatch = lazy(() => import("./roles/dispatch/DispatchPage"));
const Dock = lazy(() => import("./roles/dock/DockApp"));
const Driver = lazy(() => import("./roles/driver/DriverApp"));
const Store = lazy(() => import("./roles/store/StoreApp"));

function Home() {
  const me = useMe();
  useEffect(() => {
    if (me.data) window.location.replace(me.data.home);
    else if (me.isError) window.location.replace("/login");
  }, [me.data, me.isError]);
  return <Splash />;
}

function Splash() {
  return (
    <div className="splash" style={{ position: "fixed", inset: 0, display: "grid", placeItems: "center", background: "#F3F4F7" }}>
      <img src="/logo.png" alt="" width={56} height={56} style={{ borderRadius: "50%" }} />
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<Splash />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/dispatch/*"
          element={
            <RequireRole role="dispatcher">
              <Dispatch />
            </RequireRole>
          }
        />
        <Route
          path="/dock/*"
          element={
            <RequireRole role="loader">
              <Dock />
            </RequireRole>
          }
        />
        <Route
          path="/driver/*"
          element={
            <RequireRole role="driver">
              <Driver />
            </RequireRole>
          }
        />
        <Route
          path="/store/*"
          element={
            <RequireRole role="store">
              <Store />
            </RequireRole>
          }
        />
        <Route path="*" element={<Home />} />
      </Routes>
    </Suspense>
  );
}
