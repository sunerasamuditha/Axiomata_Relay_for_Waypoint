/** Service worker: the app shell works offline; when a new build is deployed, offer a reload. */
import { registerSW } from "virtual:pwa-register";

export function registerUpdates() {
  if (import.meta.env.DEV) return;
  const update = registerSW({
    onNeedRefresh() {
      const bar = document.createElement("div");
      bar.className = "update-bar";
      bar.innerHTML = '<span>A new version of Relay is ready.</span><button type="button">Reload</button>';
      bar.querySelector("button")!.addEventListener("click", () => update(true));
      document.body.appendChild(bar);
    },
  });
}
