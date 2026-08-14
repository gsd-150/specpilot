import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const FIXTURE_QUESTION = "Which retry requirement is stated?";
const FIXTURE_EXCERPT = "A sender MUST retry";

async function openFixture(page: Page, context: BrowserContext): Promise<Promise<string>[]> {
  const responseBodies: Promise<string>[] = [];
  page.on("response", (response) => {
    if (!new URL(response.url()).pathname.match(/^\/(?:chat|runs(?:\/|$))/)) return;
    responseBodies.push(response.text().catch(() => ""));
  });

  await page.goto("/trace");
  const session = await page.request.post("/sessions/demo");
  expect(session.status()).toBe(201);
  const credential = (await context.cookies()).find((cookie) => cookie.name === "specpilot_session");
  expect(credential?.httpOnly).toBe(true);
  return responseBodies;
}

async function selectScenario(page: Page, scenarioId: string): Promise<void> {
  const eventResponse = page.waitForResponse((response) => (
    new URL(response.url()).pathname.endsWith("/events")
  ));
  await page.getByLabel("Offline demo scenario").selectOption(scenarioId);
  await page.getByRole("button", { name: "Run selected scenario" }).click();
  expect((await eventResponse).headers()["content-type"]).toContain("text/event-stream");
}

async function assertNoSensitiveProse(page: Page, responseBodies: Promise<string>[], marker?: string): Promise<void> {
  const documentText = await page.locator("body").innerText();
  const visibleAndReturned = `${documentText}\n${(await Promise.all(responseBodies)).join("\n")}`;
  for (const sensitive of [FIXTURE_QUESTION, FIXTURE_EXCERPT, marker, "fixture answer for", "fixture-model-v1", "/Users/", "/private/"]) {
    if (sensitive !== undefined) expect(visibleAndReturned).not.toContain(sensitive);
  }
}

test("runs l1_answered through tools, egress, verifier, and SSE", async ({ page, context }) => {
  const responses = await openFixture(page, context);
  await selectScenario(page, "l1_answered");

  await expect(page.getByRole("heading", { name: "Answer verified" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Tool finished" }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Egress decision" }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Verifier checks" }).first()).toBeVisible();
  await assertNoSensitiveProse(page, responses);
});

test("runs l2_answered through compliance and semantic verification over SSE", async ({ page, context }) => {
  const responses = await openFixture(page, context);
  await selectScenario(page, "l2_answered");

  await expect(page.getByRole("heading", { name: "Answer verified" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Compliance review" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Semantic verdict" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Egress decision" }).first()).toBeVisible();
  await assertNoSensitiveProse(page, responses);
});

test("runs evidence_refused through its normal terminal path over SSE", async ({ page, context }) => {
  const responses = await openFixture(page, context);
  await selectScenario(page, "evidence_refused");

  await expect(page.getByRole("heading", { name: "The system declined to answer" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Answer outcome" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Verifier checks" }).first()).toBeVisible();
  await assertNoSensitiveProse(page, responses);
});

test("runs verifier_recovered with exactly one directed recovery over SSE", async ({ page, context }) => {
  const responses = await openFixture(page, context);
  await selectScenario(page, "verifier_recovered");

  await expect(page.getByRole("heading", { name: "Answer verified" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Directed recovery" })).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "Semantic verdict" })).toHaveCount(2);
  await assertNoSensitiveProse(page, responses);
});

test("refuses unsupported custom fixture input without entering a provider path", async ({ page, context }) => {
  const responses = await openFixture(page, context);
  const privateMarker = "browser-unsupported-private-question";
  const eventResponse = page.waitForResponse((response) => (
    new URL(response.url()).pathname.endsWith("/events")
  ));
  await page.getByLabel("L1 question").fill(privateMarker);
  await page.getByRole("button", { name: "Run L1" }).click();
  expect((await eventResponse).headers()["content-type"]).toContain("text/event-stream");

  await expect(page.getByRole("heading", { name: "The system declined to answer" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("unsupported_demo_case", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Egress decision" })).toHaveCount(0);
  await assertNoSensitiveProse(page, responses, privateMarker);
});
