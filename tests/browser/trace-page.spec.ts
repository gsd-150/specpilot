import { expect, test } from "@playwright/test";

const QUESTION = "Which retry requirement is stated?";
const FIXTURE_EXCERPT = "A sender MUST retry";

test("runs the local fixture through MCP, reservation, and a sanitized terminal trace", async ({ page, context }) => {
  await page.goto("/trace");

  const session = await page.request.post("/sessions/demo");
  expect(session.status()).toBe(201);
  const cookies = await context.cookies();
  const credential = cookies.find((cookie) => cookie.name === "specpilot_session");
  expect(credential).toBeDefined();
  expect(credential?.httpOnly).toBe(true);

  await page.getByLabel("L1 question").fill(QUESTION);
  await page.getByRole("button", { name: "Run L1" }).click();

  await expect(page.getByRole("heading", { name: "Answer verified" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Tool finished" }).first()).toBeVisible();
  await expect(page.getByText("Reservation ID", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("answered", { exact: true }).first()).toBeVisible();

  expect(await page.getByLabel("L1 question").inputValue()).toBe("");
  const documentText = await page.locator("body").innerText();
  expect(documentText).not.toContain(QUESTION);
  expect(documentText).not.toContain(FIXTURE_EXCERPT);
  expect(documentText).not.toContain(credential?.value ?? "unreachable-cookie");
  expect(documentText).not.toContain("/Users/");
  expect(documentText).not.toContain("/private/");
  expect(documentText).not.toContain("fixture answer for");
  expect(documentText).not.toContain("fixture-model-v1");
});
