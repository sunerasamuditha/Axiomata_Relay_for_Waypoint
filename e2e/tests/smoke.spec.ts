/**
 * Smoke test: fast checks to run after every deploy (about 30 seconds).
 *
 *   E2E_BASE_URL=https://relay-xxxx.a.run.app pnpm --filter @relay/e2e test:smoke
 */
import { expect, test } from "@playwright/test";
import { newSandbox, PASSWORD, PEOPLE, signIn, WEB } from "./support";

test("health: the API is up, the database answers, the demo is seeded", async ({ request }) => {
  const res = await request.get("/api/health");
  expect(res.ok()).toBeTruthy();
  const health = await res.json();
  expect(health).toMatchObject({ status: "ok", db: "ok", seeded: true });
});

test("every role signs in and lands on its own face", async ({ browser, baseURL, request }) => {
  const code = await newSandbox(request);
  const problems: string[] = [];
  const landmarks: Record<string, string> = {
    [PEOPLE.dispatcher.email]: ".cmdbar [data-act=plan]",
    [PEOPLE.loader.email]: ".dempty h2:has-text('No plan yet')", // a fresh sandbox: orders are still open
    [PEOPLE.driver.email]: "a[href='/driver/me']",
    [PEOPLE.store.email]: "a[href='/store/order']",
  };
  for (const who of Object.values(PEOPLE)) {
    const page = await signIn(browser, baseURL!, who, code, problems);
    await expect(page.locator(landmarks[who.email]).first()).toBeVisible({ timeout: 60_000 });
    await page.context().close();
  }
  expect(problems, "uncaught errors in the pages").toEqual([]);
});

test("a signed-out visitor is sent to the sign-in page", async ({ page }) => {
  await page.goto("/dispatch");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.locator("#email")).toBeVisible();
});

test("roles are enforced by the server, not just hidden in the UI", async ({ request }) => {
  const login = await request.post("/api/auth/login", { headers: WEB, data: { email: PEOPLE.store.email, password: PASSWORD } });
  expect(login.ok()).toBeTruthy();
  expect((await request.get("/api/dispatch/snapshot")).status()).toBe(403);
  expect((await request.get("/api/driver/run")).status()).toBe(403);
  // writes without the client header are refused (CSRF guard)
  expect((await request.post("/api/store/notices/read", { data: {} })).status()).toBe(403);
});
