# W3 Part 3: React Trace Page and Release Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and package a minimal React page that creates an L1 run, polls its owner-scoped sanitized trace, and makes refusal, disclosure blocking, provider failure, and interruption visibly distinct.

**Architecture:** A small TypeScript/Vite application consumes only the W3 REST endpoints. A bounded polling hook stops at terminal states or a client deadline; static build assets are packaged into the Python wheel and served by FastAPI. No SSE or corpus text enters the page.

**Tech Stack:** Node.js 22, React 19, TypeScript 5.9, Vite 7, Vitest, Testing Library, Playwright, FastAPI static files, pytest.

## Global Constraints

- Depends on the API shapes in `2026-08-11-w3-2-api-runs.md`.
- No SSE, EventSource, WebSocket, account system, dashboard, or workflow editor.
- Session credentials use Authorization/header or HTTP-only cookie and never enter a URL.
- Client timeout is not a server state and must not overwrite the last server status.
- The UI never renders query history, clause text, excerpts, candidate bodies, credentials, paths, or raw provider messages.
- Static assets must be included in wheel/sdist and work from an installed package outside the repository.
- For the final gate, recreate exact throwaway databases `specpilot_w3_release_test` and `specpilot_w3_smoke_test`, and start Qdrant as `docker run --rm -d --name specpilot-w3-qdrant -p 127.0.0.1:6334:6333 qdrant/qdrant:v1.12.4`; do not reuse a live service.

---

## File map

- `web/trace/package.json`, `tsconfig.json`, `vite.config.ts`, `playwright.config.ts` — locked frontend toolchain, browser fixture, and output path.
- `web/trace/src/api.ts` — exact REST types/client.
- `web/trace/src/useRunPolling.ts` — polling deadline and terminal stop.
- `web/trace/src/App.tsx` — question form and trace page composition.
- `web/trace/src/components/StatusPanel.tsx` — distinct terminal semantics.
- `web/trace/src/components/TraceTimeline.tsx` — sanitized events only.
- `src/specpilot/api/static.py` and `src/specpilot/api/app.py` — installed-resource static serving and SPA entry route.
- `pyproject.toml`, `Makefile`, Dockerfiles — package assets and frontend gates.

### Task 1: Frontend contract and build skeleton

**Files:**
- Create: `web/trace/package.json`
- Create: `web/trace/package-lock.json`
- Create: `web/trace/tsconfig.json`
- Create: `web/trace/vite.config.ts`
- Create: `web/trace/playwright.config.ts`
- Create: `web/trace/index.html`
- Create: `web/trace/src/api.ts`
- Test: `web/trace/src/api.test.ts`

**Interfaces:**
- Produces: `createRun`, `getRun`, `RunView`, `RunEvent`, and `TerminalStatus`.

- [ ] **Step 1: Write the API decode RED**

```ts
it("rejects a trace containing plaintext fields", () => {
  expect(() => decodeRun({
    run_id: crypto.randomUUID(), status: "running", reason: null,
    events: [{ kind: "tool_finished", sequence: 1, tool: "get_clause", query: "hidden" }]
  })).toThrow("unexpected trace field");
});
```

- [ ] **Step 2: Run the RED**

Run: `npm --prefix web/trace test -- --run`

Expected: FAIL because the frontend package/API module does not exist.

- [ ] **Step 3: Create the locked minimal package**

Use React/React DOM 19, TypeScript 5.9, Vite 7, Vitest, jsdom, Testing Library, and `@playwright/test`. Define `test`, `test:browser`, and `build` scripts plus a Playwright web-server command that starts only the fixture-profile API. Define a closed decoder matching the server's status/event union; reject unknown event fields instead of spreading server objects into components. Send bearer credentials only in `Authorization` and use `credentials: "same-origin"` for cookie mode.

- [ ] **Step 4: Generate lockfile and run GREEN**

Run: `npm --prefix web/trace install && npm --prefix web/trace test -- --run && npm --prefix web/trace run build`

Expected: PASS and `src/specpilot/api/static/trace/index.html` is produced.

- [ ] **Step 5: Commit**

```bash
git add web/trace src/specpilot/api/static/trace
git commit -m "feat: scaffold typed React trace client"
```

### Task 2: Bounded polling lifecycle

**Files:**
- Create: `web/trace/src/useRunPolling.ts`
- Test: `web/trace/src/useRunPolling.test.tsx`

**Interfaces:**
- Produces: `useRunPolling({runId, token, intervalMs, deadlineMs})` returning `serverRun`, `connectionState`, `refresh`.

- [ ] **Step 1: Write terminal and deadline REDs**

```ts
it.each(["answered", "refused", "egress_blocked", "failed", "interrupted"])(
  "stops polling on %s", async (status) => {
    fetchMock.mockResolvedValue(response(runView(status)));
    renderHook(() => useRunPolling({runId: "r1", token: "t", intervalMs: 10, deadlineMs: 1000}));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    vi.advanceTimersByTime(100);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  }
);

it("keeps the last server status when the client deadline expires", async () => {
  fetchMock.mockResolvedValue(response(runView("running")));
  const hook = renderHook(() => useRunPolling({runId: "r1", token: "t", intervalMs: 10, deadlineMs: 25}));
  vi.advanceTimersByTime(30);
  expect(hook.result.current.serverRun?.status).toBe("running");
  expect(hook.result.current.connectionState).toBe("poll_timeout");
});
```

- [ ] **Step 2: Run the RED**

Run: `npm --prefix web/trace test -- --run src/useRunPolling.test.tsx`

Expected: FAIL because the hook does not exist.

- [ ] **Step 3: Implement abortable bounded polling**

Use one `AbortController` per request, schedule the next request only after the previous completes, stop on unmount/terminal/deadline, and expose manual `refresh` without resetting the server state. Do not place the token or run ID in query parameters; run ID remains a path segment.

- [ ] **Step 4: Run hook GREEN**

Run: `npm --prefix web/trace test -- --run src/useRunPolling.test.tsx`

Expected: PASS with fake timers and no pending timer warning.

- [ ] **Step 5: Commit**

```bash
git add web/trace/src/useRunPolling.ts web/trace/src/useRunPolling.test.tsx
git commit -m "feat: bound trace page polling"
```

### Task 3: Trace UI and distinct terminal semantics

**Files:**
- Create: `web/trace/src/main.tsx`
- Create: `web/trace/src/App.tsx`
- Create: `web/trace/src/components/StatusPanel.tsx`
- Create: `web/trace/src/components/TraceTimeline.tsx`
- Create: `web/trace/src/styles.css`
- Test: `web/trace/src/App.test.tsx`

**Interfaces:**
- Consumes: Part 2 `POST /chat`, `GET /runs/{run_id}`, and the polling hook.

- [ ] **Step 1: Write semantic rendering REDs**

```tsx
it.each([
  ["refused", "The system declined to answer"],
  ["egress_blocked", "Disclosure gate blocked the send"],
  ["failed", "Provider execution failed"],
  ["interrupted", "Run was interrupted"],
])("renders %s distinctly", async (status, copy) => {
  render(<App api={fakeApiWithTerminal(status)} />);
  await userEvent.click(screen.getByRole("button", {name: "Run L1"}));
  expect(await screen.findByText(copy)).toBeVisible();
});
```

- [ ] **Step 2: Run the RED**

Run: `npm --prefix web/trace test -- --run src/App.test.tsx`

Expected: FAIL because UI components do not exist.

- [ ] **Step 3: Implement the one-page trace view**

Add a bounded question form, status/reason panel, event timeline, reservation/ledger summary, evidence IDs/hashes, and verifier checks. Render tool names, argument keys, result counts, durations, retries, and stable errors only. Use accessible status text in addition to color; do not render source/excerpt/candidate fields even if a malformed object bypasses compile-time types.

- [ ] **Step 4: Run component tests and production build**

Run: `npm --prefix web/trace test -- --run && npm --prefix web/trace run build`

Expected: PASS; built JavaScript contains no literal `EventSource` or `/events` endpoint.

- [ ] **Step 5: Commit**

```bash
git add web/trace
git commit -m "feat: render sanitized L1 run traces"
```

### Task 4: Package and serve installed static assets

**Files:**
- Create: `src/specpilot/api/static.py`
- Modify: `src/specpilot/api/app.py`
- Modify: `pyproject.toml`
- Modify: `Makefile`
- Modify: `docker/api.Dockerfile`
- Test: `tests/unit/api/test_static_trace.py`
- Test: `tests/integration/api/test_trace_assets.py`

**Interfaces:**
- Produces: `GET /trace` and immutable hashed asset responses from package resources.

- [ ] **Step 1: Write installed-resource RED**

```python
def test_trace_page_comes_from_package_resources(app_client):
    page = app_client.get("/trace")
    assert page.status_code == 200
    assert '<div id="root"></div>' in page.text
    asset = app_client.get(extract_hashed_asset(page.text))
    assert asset.status_code == 200
    assert asset.headers["x-content-type-options"] == "nosniff"
```

- [ ] **Step 2: Run the RED**

Run: `.venv/bin/python -m pytest tests/unit/api/test_static_trace.py tests/integration/api/test_trace_assets.py -q`

Expected: FAIL because assets are not packaged or served.

- [ ] **Step 3: Add deterministic build/package hooks**

Package `specpilot.api/static/trace/**`, serve `index.html` at `/trace`, serve hashed assets under `/trace/assets`, set `nosniff` and a restrictive content-security policy, and add `frontend-test`/`frontend-build` Make targets. The Docker build runs `npm ci`, builds assets before the wheel, and copies no Node runtime into the final image.

- [ ] **Step 4: Prove wheel and sdist contain assets**

Run: `npm --prefix web/trace ci && make frontend-test frontend-build && .venv/bin/python -m build && unzip -l dist/*.whl | rg "specpilot/api/static/trace/.+\.(html|js|css)"`

Expected: PASS with HTML, hashed JS, and CSS present.

- [ ] **Step 5: Commit**

```bash
git add src/specpilot/api pyproject.toml Makefile docker/api.Dockerfile tests/unit/api/test_static_trace.py tests/integration/api/test_trace_assets.py
git commit -m "feat: package the React trace page"
```

### Task 5: Browser closure and full release gate

**Files:**
- Create: `tests/browser/trace-page.spec.ts`
- Modify: `web/trace/package.json`
- Modify: `web/trace/package-lock.json`
- Modify: `web/trace/playwright.config.ts`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/handoff/2026-08-11-codex-handoff.md`
- Create: `docs/reports/w3-mcp-api-trace-report.md`

- [ ] **Step 1: Add browser-flow RED**

The browser test opens `/trace`, obtains the fixture-only session, submits one question, observes at least one MCP tool event and one reservation, reaches a terminal state, and asserts the rendered document contains neither the submitted question nor fixture excerpt text after the form is cleared.

- [ ] **Step 2: Run the browser RED against the local fixture app**

Run: `npm --prefix web/trace run test:browser`

Expected: FAIL until the test runner and fixture app lifecycle are wired.

- [ ] **Step 3: Wire CI and documentation**

Run frontend tests/build before Python packaging; install the built wheel in a clean virtual environment; run the browser fixture only against local fake-provider data. Document `POST /chat`, ownership, status meanings, polling limit, migration 004, explicit policy rebind, and the fact that SSE remains W5.

- [ ] **Step 4: Run fresh complete evidence**

Run, with newly created isolated services:

```bash
make check
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_w3_release_test SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6334 make integration-db
SPECPILOT_TEST_QDRANT_URL=http://127.0.0.1:6334 make integration-qdrant
SPECPILOT_TEST_DSN=postgresql://localhost:5432/specpilot_w3_smoke_test make fixture-smoke
npm --prefix web/trace test -- --run
npm --prefix web/trace run build
npm --prefix web/trace run test:browser
.venv/bin/python -m build
```

Expected: zero failures and zero unexplained skips. Install the wheel into a new temporary virtual environment and verify `create_app`, MCP tool listing, `/trace`, and package assets from outside the repository.

- [ ] **Step 5: Run final safety audit**

Run:

```bash
git diff --check
rg -n "query|excerpt|candidate|authorization|secret|api[_-]?key|/Users/|/private/" src/specpilot/runs src/specpilot/api web/trace/src docs/reports/w3-mcp-api-trace-report.md
git status --ignored --short
```

Review every hit. Confirm `artifacts/restricted/`, `manifests/local/`, `data/`, and `tmp/` remain ignored and absent from the index; verify all temporary PostgreSQL databases, Qdrant collections/containers, browser state, and build-install environments are removed without touching `specpilot_live`.

- [ ] **Step 6: Write report and commit**

Record exact commit range, test counts, database/service isolation, state regressions, installed-package proof, safety scan, limitations, and no-real-provider status in `docs/reports/w3-mcp-api-trace-report.md` and update the handoff.

```bash
git add .github/workflows/ci.yml README.md docs/handoff/2026-08-11-codex-handoff.md docs/reports/w3-mcp-api-trace-report.md tests/browser
git commit -m "docs: close the W3 MCP API trace slice"
```
