import { expect, test } from "./fixtures/test-base";
import { waitForDispatcherIdle } from "./fixtures/helpers";

test("a low-completeness verifier score parks the workspace with a structured blocker", async ({
  page,
  request,
}) => {
  const title = `e2e-low-completeness-${Date.now()}`;

  const create = await request.post("/api/workspaces", {
    data: {
      title,
      description: "verifier reports low completeness on this research run",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: { team: "research" },
  });
  expect(dispatch.ok()).toBeTruthy();

  await waitForDispatcherIdle(request);

  await expect
    .poll(
      async () => {
        const res = await request.get(`/api/workspaces/${taskId}/invocations/inv_001`);
        if (!res.ok()) return "pending";
        const doc = (await res.json()) as { status: string };
        return doc.status;
      },
      { timeout: 30_000, intervals: [200, 500, 1000] },
    )
    .toBe("success");

  await expect
    .poll(
      async () => {
        const res = await request.get(`/api/workspaces/${taskId}`);
        if (!res.ok()) return "pending";
        const ws = (await res.json()) as { status: string };
        return ws.status;
      },
      { timeout: 10_000, intervals: [200, 500] },
    )
    .toBe("awaiting_input");

  const wsRes = await request.get(`/api/workspaces/${taskId}`);
  expect(wsRes.ok()).toBeTruthy();
  const ws = (await wsRes.json()) as {
    latest_handover: { verifier_completeness: number | null } | null;
    blockers?: { kind: string; name: string; detail: string }[];
  };
  expect(ws.latest_handover?.verifier_completeness).toBe(0.45);
  const lowCompletenessBlocker = ws.blockers?.find((b) => b.name === "low_completeness");
  expect(lowCompletenessBlocker).toBeDefined();
  expect(lowCompletenessBlocker?.detail).toContain("0.45");

  await page.goto(`/workspaces/${taskId}`);
  await expect(page.getByTestId("status-awaiting_input")).toBeVisible();
  const blockerItem = page.getByTestId("blocker-item").filter({ hasText: "low_completeness" });
  await expect(blockerItem).toBeVisible();
  await expect(blockerItem.getByTestId("blocker-detail")).toContainText("0.45");
});

test("a completed research run persists its synthesis as a downloadable report artifact", async ({
  page,
  request,
}) => {
  const title = `e2e-research-report-${Date.now()}`;

  const create = await request.post("/api/workspaces", {
    data: {
      title,
      description: "playwright drove this research run",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: { team: "research" },
  });
  expect(dispatch.ok()).toBeTruthy();

  await waitForDispatcherIdle(request);

  await expect
    .poll(
      async () => {
        const res = await request.get(`/api/workspaces/${taskId}/invocations/inv_001`);
        if (!res.ok()) return "pending";
        const doc = (await res.json()) as { status: string };
        return doc.status;
      },
      { timeout: 30_000, intervals: [200, 500, 1000] },
    )
    .toBe("success");

  const wsRes = await request.get(`/api/workspaces/${taskId}`);
  expect(wsRes.ok()).toBeTruthy();
  const ws = (await wsRes.json()) as {
    latest_handover: {
      artifacts_produced: { path: string; description: string }[];
    } | null;
  };
  const artifacts = ws.latest_handover?.artifacts_produced ?? [];
  expect(artifacts.length).toBeGreaterThan(0);
  const reportArtifact = artifacts[0]!;
  expect(reportArtifact.path).toBe("invocations/inv_001/report.md");

  const artifactRes = await request.get(
    `/api/workspaces/${taskId}/artifacts?path=${encodeURIComponent(reportArtifact.path)}`,
  );
  expect(artifactRes.ok()).toBeTruthy();
  const reportText = await artifactRes.text();
  expect(reportText).toContain(`STUB INVOCATION SYNTHESIS for: ${title}`);

  await page.goto(`/workspaces/${taskId}`);
  await expect(page.getByRole("heading", { name: "Deliverables" })).toBeVisible();
  const reportLink = page.getByRole("link", { name: reportArtifact.description });
  await expect(reportLink).toBeVisible();
  await expect(reportLink).toHaveAttribute("href", /invocations(?:\/|%2F).*report\.md/);
});
