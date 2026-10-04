/**
 * The judge walkthrough from the README, end to end, across all four roles at once:
 *
 *   store orders → dispatcher plans, compares policies, publishes → loader loads and flags a
 *   shortfall → dispatcher decides → loader releases → driver delivers with proof → the van goes
 *   dark, the dispatcher queues a move → the driver reconnects and physical facts win → the store
 *   confirms receipt with a damaged case → dispatch sees the issue.
 *
 * Each step checks the UI and, where it matters, the server state through the same session.
 * It runs in a private sandbox workspace, so it never disturbs the shared demo.
 */
import { expect, test, type Page } from "@playwright/test";
import { deliverNextStop, getJson, newSandbox, PEOPLE, signIn, swipe } from "./support";

const snapshot = (d: Page) => getJson(d, "/api/dispatch/snapshot");
const tripOf = async (d: Page, vehicle: string) => ((await snapshot(d)).trips as any[]).find((t) => t.vehicle === vehicle);
const feedTitles = async (d: Page) => ((await snapshot(d)).feed as any[]).map((n) => String(n.title));

test("one plan, four faces: the full walkthrough", async ({ browser, baseURL, request }) => {
  const code = await newSandbox(request);
  const problems: string[] = [];
  const base = baseURL!;
  const slow = { timeout: 120_000, intervals: [1_000, 2_000, 3_000] };

  // ── 1. Store manager places an order before the 4 PM cutoff ─────────────────────────────────
  const store = await signIn(browser, base, PEOPLE.store, code, problems);
  // Fathima's delivery PIN, from her own profile: she types it on the driver's phone at her door
  const pin = String((await getJson(store, "/api/store/profile")).delivery_pin);
  await test.step("store places an order", async () => {
    await store.locator("a[href='/store/order']").first().click();
    await store.locator(".orow:has-text('Yoghurt') button[aria-label^='More']").click();
    await store.getByRole("button", { name: /Place order/ }).click();
    await expect
      .poll(async () => {
        const home = await getJson(store, "/api/store/home");
        return [...home.deliveries, ...home.upcoming].some((o: any) => o.channel === "app" && o.status === "placed");
      }, slow)
      .toBe(true);
  });

  // ── 2. Dispatcher closes orders, plans, compares policies, publishes ────────────────────────
  const desk = await signIn(browser, base, PEOPLE.dispatcher, code, problems);
  await test.step("dispatcher closes the orders and the engine plans the day", async () => {
    await desk.locator(".cmdbar [data-act=plan]").click();
    await expect.poll(async () => (await snapshot(desk)).plan?.status ?? null, slow).toBe("draft");
    await expect(desk.locator("#busy")).toHaveCount(0, { timeout: 60_000 });
  });

  await test.step("deferral lever: pick Fairness first, apply, publish", async () => {
    await desk.locator("#drail [data-act='dview:lever']").click();
    await expect(desk.locator(".bignums").first()).toBeVisible({ timeout: 120_000 });
    await desk.locator("[data-act='lever:fairness']").click();
    await desk.locator("[data-act=leverapply]").click();
    await expect.poll(async () => (await snapshot(desk)).plan?.policy ?? null, slow).toBe("fairness");
    await expect(desk.locator("#busy")).toHaveCount(0, { timeout: 60_000 });
    await desk.locator(".cmdbar [data-act=publish]").click();
    await expect.poll(async () => (await snapshot(desk)).plan?.status ?? null, slow).toBe("published");
  });

  await test.step("the store sees its delivery planned", async () => {
    await expect.poll(async () => ((await getJson(store, "/api/store/home")).deliveries as any[]).some((o) => o.status === "planned"), slow).toBe(true);
  });

  // ── 3. Loader: reverse stop order, flag a shortfall ─────────────────────────────────────────
  const dock = await signIn(browser, base, PEOPLE.loader, code, problems);
  await test.step("loader opens VEH057, loads three lines and flags a missing one", async () => {
    await expect(dock.locator(".qrow").first()).toBeVisible({ timeout: 30_000 });
    await dock.locator(".ctile").first().click(); // "Who is loading?" is asked once per device
    await expect(dock.locator(".ctile")).toHaveCount(0);
    await dock.locator(".qrow:has-text('VEH057')").click();
    for (let i = 0; i < 3; i++) {
      await dock.locator(".lrow.todo[role=button]").first().click();
      await dock.waitForTimeout(250);
    }
    const store105 = dock.locator(".sg:has-text('Nuwara Eliya Town') .lrow.todo .fl").first();
    await ((await store105.count()) ? store105 : dock.locator(".lrow.todo .fl").first()).click();
    await dock.locator(".drawer .df button").click();
    await expect.poll(async () => ((await snapshot(desk)).issues as any[]).some((i) => i.kind === "short_at_dock" && i.status === "open"), slow).toBe(true);
  });

  // ── 4. Dispatcher decides; everyone is told ─────────────────────────────────────────────────
  await test.step("dispatcher takes the recommended decision", async () => {
    await desk.locator(".fcard button:has-text('Decide')").first().click();
    await desk.locator("#modal .opt.rec").click();
    await expect.poll(async () => ((await snapshot(desk)).issues as any[]).some((i) => i.kind === "short_at_dock" && i.status === "decided"), slow).toBe(true);
  });

  await test.step("the decision reaches the dock; the loader finishes and releases", async () => {
    const gotIt = dock.locator(".strip.blue button:has-text('Got it')").first();
    await expect(gotIt).toBeVisible({ timeout: 30_000 });
    await gotIt.click();
    for (let i = 0; i < 60; i++) {
      const all = dock.locator("button:has-text('lines are on')");
      if (await all.count()) {
        await all.first().click();
        await dock.waitForTimeout(300);
        continue;
      }
      const row = dock.locator(".lrow.todo[role=button]");
      if (await row.count()) {
        await row.first().click();
        await dock.waitForTimeout(200);
        continue;
      }
      break;
    }
    const release = dock.getByRole("button", { name: /Check and release/ });
    await expect(release).toBeEnabled({ timeout: 30_000 });
    await release.click();
    await dock.fill("#seal", "KH-58213");
    await dock.locator("button[role=switch]").click(); // doors closed and locked
    await swipe(dock);
    await expect.poll(async () => (await tripOf(desk, "VEH057"))?.status, slow).toBe("loaded");
  });

  // ── 5. Driver: start, deliver with proof ────────────────────────────────────────────────────
  const van = await signIn(browser, base, PEOPLE.driver, code, problems);
  await test.step("driver starts the run and delivers stop 1 with a photo and the store manager's PIN", async () => {
    await van.getByRole("button", { name: /Start run/ }).click();
    await expect.poll(async () => (await tripOf(desk, "VEH057"))?.status, slow).toBe("out");
    await deliverNextStop(van, pin);
    await expect
      .poll(async () => {
        const run = await getJson(van, "/api/driver/run");
        return (run.trips[0].visits as any[]).filter((v) => ["delivered", "partial"].includes(v.status)).length;
      }, slow)
      .toBeGreaterThan(0);
  });

  // ── 6. Dark corridor ────────────────────────────────────────────────────────────────────────
  await test.step("the van loses signal; the phone keeps working; the desk shows No signal", async () => {
    await van.locator("a[href='/driver/me']").click();
    await van.locator("button[aria-label='Test offline mode']").click();
    await van.locator("a[href='/driver']").click();
    await deliverNextStop(van, pin); // recorded on the phone only
    await expect.poll(async () => (await tripOf(desk, "VEH057"))?.signal, { timeout: 180_000, intervals: [3_000] }).toBe("dark");
    await expect.poll(() => desk.locator(".tn.dark").count(), slow).toBeGreaterThan(0);
  });

  await test.step("the dispatcher queues a move for the dark van", async () => {
    await desk.fill("#dsearch", "VEH057");
    await desk.press("#dsearch", "Enter");
    await desk.locator(".dc .iacts button").last().click();
    await expect(desk.locator("#modal .opt").first()).toBeVisible({ timeout: 30_000 });
    const recommended = desk.locator("#modal .opt.rec");
    await ((await recommended.count()) ? recommended : desk.locator("#modal .opt").first()).click();
    await expect.poll(async () => ((await snapshot(desk)).pending_moves as any[]).length, slow).toBeGreaterThan(0);
  });

  await test.step("back online: every record lands with its own time and physical facts win", async () => {
    while (await van.getByText("Swipe when you arrive").count()) await deliverNextStop(van, pin);
    await van.locator("a[href='/driver/me']").click();
    await van.locator("button[aria-label='Test offline mode']").click(); // signal is back
    await van.locator("a[href='/driver/sync']").click();
    await expect
      .poll(async () => (await feedTitles(desk)).some((t) => t === "Conflict resolved: delivery kept" || t.startsWith("Move applied")), slow)
      .toBe(true);
    await expect.poll(async () => (await tripOf(desk, "VEH057"))?.signal, slow).toBe("ok");
  });

  // ── 7. Store confirms what arrived ──────────────────────────────────────────────────────────
  await test.step("the store confirms receipt and reports a damaged case; dispatch hears it", async () => {
    await store.goto("/store");
    const confirm = store.getByRole("button", { name: /Confirm receipt/ }).first();
    await expect(confirm).toBeVisible({ timeout: 60_000 });
    await confirm.click();
    await store.locator(".nchip:has-text('Damaged')").first().click();
    await store.locator(".rrow button[aria-label='Fewer']").first().click();
    await store.getByRole("button", { name: /Confirm and report/ }).click();
    await expect.poll(async () => (await feedTitles(desk)).some((t) => t.startsWith("Receipt issue")), slow).toBe(true);
  });

  expect(problems, "uncaught errors in the pages").toEqual([]);
});
