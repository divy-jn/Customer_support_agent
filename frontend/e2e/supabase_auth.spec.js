import { expect, test } from "@playwright/test";

const email = "gina@example.test";
const password = "test-only-password";
const customer = { id: 17, name: "Gina", email, role: "customer" };

function appToken() {
  const claims = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  return `test.${claims}.test`;
}

async function mockSignIn(page, principal, portal) {
  const token = appToken();
  const authCalls = [];
  await page.route("https://auth.example.test/auth/v1/token?grant_type=password", async (route) => {
    authCalls.push(route.request().postDataJSON());
    expect(route.request().headers().apikey).toBe("sb_publishable_example_test");
    await route.fulfill({ json: { access_token: "supabase-test-token" } });
  });
  await page.route("**/api/auth/supabase/exchange", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer supabase-test-token");
    expect(route.request().postDataJSON()).toEqual({ portal });
    await route.fulfill({ json: { access_token: token, principal } });
  });
  return { token, authCalls };
}

async function submitSignIn(page) {
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

test("customer signs in before opening chat and sees an authenticated history error", async ({ page }) => {
  const { token, authCalls } = await mockSignIn(page, customer, "customer");
  const socketTokens = [];
  await page.routeWebSocket(/\/ws\/chat(?:\?|\/)/, (socket) => {
    socketTokens.push(new URL(socket.url()).searchParams.get("token"));
  });
  await page.route("**/api/v1/customers/17/history", async (route) => {
    expect(route.request().headers().authorization).toBe(`Bearer ${token}`);
    await route.fulfill({ status: 403, json: { detail: "Access denied" } });
  });
  await page.goto("/");
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  expect(authCalls).toEqual([]);
  expect(socketTokens).toEqual([]);

  await submitSignIn(page);
  await expect(page.locator("header").getByText("Online")).toBeVisible();
  expect(authCalls).toEqual([{ email, password }]);
  expect(socketTokens).toEqual([token]);
  await page.getByRole("button", { name: /Gina/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Could not load your history (403)." })).toBeVisible();
});

test("staff authenticates directory, search and history and sees the admin role boundary", async ({ page }) => {
  const { token } = await mockSignIn(page, { role: "agent", email }, "staff");
  const paths = [];
  await page.route("**/api/v1/customers**", async (route) => {
    expect(route.request().headers().authorization).toBe(`Bearer ${token}`);
    const url = new URL(route.request().url());
    paths.push(url.pathname);
    if (url.pathname.endsWith("/history")) {
      await route.fulfill({ status: 503, json: { detail: "Unavailable" } });
    } else {
      await route.fulfill({ json: [customer] });
    }
  });
  await page.goto("/agent/customers");
  await expect(page.getByRole("heading", { name: "Staff sign-in" })).toBeVisible();
  expect(paths).toEqual([]);
  await submitSignIn(page);
  const customerEntry = page.getByRole("button", { name: /Gina/ });
  await expect(customerEntry).toBeVisible();
  await page.getByPlaceholder("Search name or email...").fill("Gina");
  await expect.poll(() => paths.includes("/api/v1/customers/search")).toBe(true);
  await customerEntry.click();
  await expect(page.getByRole("alert").filter({ hasText: "Could not load customer history (503)." })).toBeVisible();
  expect(paths).toEqual(expect.arrayContaining(["/api/v1/customers", "/api/v1/customers/17/history"]));
  await page.goto("/admin/settings");
  await expect(page.getByRole("alert").filter({ hasText: "403" })).toBeVisible();
});
