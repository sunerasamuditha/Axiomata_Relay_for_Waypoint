import { expect, type APIRequestContext, type Browser, type Page } from "@playwright/test";

export const PASSWORD = process.env.DEMO_PASSWORD ?? "relay2026";
/** Every write must carry this header (the API's CSRF guard). */
export const WEB = { "X-Relay-Client": "web" };

export type Person = {
  email: string;
  home: string;
  viewport: { width: number; height: number };
  mobile?: boolean;
};

/** The four judge accounts, each on the device it is designed for. */
export const PEOPLE = {
  store: { email: "fathima@waypoint.lk", home: "/store", viewport: { width: 1280, height: 820 } },
  dispatcher: { email: "nirosha@waypoint.lk", home: "/dispatch", viewport: { width: 1440, height: 900 } },
  loader: { email: "kasun@waypoint.lk", home: "/dock", viewport: { width: 1180, height: 820 } },
  driver: { email: "sunil@waypoint.lk", home: "/driver", viewport: { width: 390, height: 844 }, mobile: true },
} satisfies Record<string, Person>;

/** A private copy of the demo day. Nobody else sees what the test does in it. */
export async function newSandbox(request: APIRequestContext): Promise<string> {
  const res = await request.post("/api/demo/sandboxes", { headers: WEB });
  expect(res.ok(), `creating a sandbox failed: ${res.status()} ${await res.text()}`).toBeTruthy();
  const body = (await res.json()) as { code: string };
  return body.code;
}

/** Signs in through the real login page, in its own browser context (own cookies and storage). */
export async function signIn(browser: Browser, baseURL: string, who: Person, code: string, problems: string[]): Promise<Page> {
  const mobile = !!who.mobile;
  const context = await browser.newContext({ baseURL, viewport: who.viewport, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: mobile ? 2 : 1 });
  const page = await context.newPage();
  page.on("pageerror", (e) => problems.push(`${who.email}: ${e.message}`));
  await page.goto("/login");
  await page.fill("#email", who.email);
  await page.fill("#pw", PASSWORD);
  await page.getByRole("button", { name: "I have a demo workspace code" }).click();
  await page.fill("#code", code);
  await page.click("button[type=submit]");
  await page.waitForURL((u) => u.pathname.startsWith(who.home), { timeout: 30_000 });
  return page;
}

/** GET through the page's own session (same cookies as the signed-in user). */
export async function getJson<T = any>(page: Page, path: string): Promise<T> {
  const res = await page.request.get(path);
  expect(res.ok(), `GET ${path}: ${res.status()}`).toBeTruthy();
  return (await res.json()) as T;
}

/** The swipe controls also answer Enter, which keeps the tests independent of pixel positions. */
export async function swipe(page: Page): Promise<void> {
  const control = page.locator(".swipe:not(.locked)").first();
  await expect(control).toBeVisible();
  await control.focus();
  await page.keyboard.press("Enter");
}

/** Driver: arrive, use a sample proof photo, hand over for the store manager's PIN where asked, swipe to complete. */
export async function deliverNextStop(page: Page, pin?: string): Promise<void> {
  await swipe(page); // "Swipe when you arrive"
  await page.getByText("Next: proof of delivery").click();
  await page.locator(".upl").click(); // sample photo (no camera in a headless browser)
  const field = page.locator("#pin");
  if (await field.isVisible()) {
    if (!pin) throw new Error("This stop asks for the store manager's delivery PIN, but none was passed to deliverNextStop().");
    await field.fill(pin);
    await page.getByRole("button", { name: "Confirm PIN" }).click();
    await expect(page.getByText("Confirmed by")).toBeVisible();
  }
  await swipe(page); // "Swipe to complete"
  await page.waitForTimeout(800);
}
