import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";
import { withDefaultEngineTransportCredential } from "./fixtures/transport-credential";

const CANNED_DELIVERY = JSON.stringify({
  url: "https://github.com/DejvMatikar/alan/pull/999",
  title: "feat: delivery summary panel",
  mergedAt: "2026-05-25T00:00:00Z",
  files: [{ path: "alan/web/src/components/DeliverySummaryPanel.tsx" }],
  commits: [
    {
      oid: "abc1234abc1234abc1234abc1234abc1234abc123",
      messageHeadline: "feat: add delivery summary panel",
    },
  ],
});

const SYNTHESIS = `## What worked
Things went smoothly

## What didn't
Some friction

## How to verify in the UI
1. Verification step alpha
2. Verification step beta

## Action items
[HIGH] Change: Refactor X | Where: alan/foo.py | Effort: medium
[MED] Change: Add Y test | Where: tests/ | Effort: low
`;

function seedHandover(
  handoverDir: string,
  taskId: string,
  seq: number,
  fields: {
    from_team: string;
    handover_type?: string;
    full_synthesis?: string;
    pr_number?: number;
    test_scenarios?: {
      id: string;
      description: string;
      given: string;
      when: string;
      then: string;
    }[];
  },
): void {
  const seqStr = String(seq).padStart(3, "0");
  const id = `h-${taskId}-${seqStr}`;
  const ht = fields.handover_type ?? "forward";
  const synthesis = fields.full_synthesis ?? "";
  const ts = fields.test_scenarios ?? [];

  const synthesisBlock =
    synthesis.length > 0
      ? `full_synthesis: |\n${synthesis
          .split("\n")
          .map((l) => `  ${l}`)
          .join("\n")}`
      : `full_synthesis: ''`;

  const prBlock =
    fields.pr_number !== undefined ? `\npr_number: ${fields.pr_number}` : "";

  const scenariosBlock =
    ts.length > 0
      ? `\ntest_scenarios:\n${ts
          .map(
            (s) =>
              `  - id: ${s.id}\n    description: "${s.description}"\n    given: "${s.given}"\n    when: "${s.when}"\n    then: "${s.then}"`,
          )
          .join("\n")}`
      : "";

  const content = `schema_version: '1.6'
handover_id: ${id}
handover_chain_id: ${id}
handover_type: ${ht}
task_id: ${taskId}
from_team: ${fields.from_team}
from_invocation_id: inv_001
iteration: 1
timestamp_utc: 2026-05-25T00:00:00Z
${synthesisBlock}${prBlock}${scenariosBlock}
`;

  fs.writeFileSync(
    path.join(handoverDir, `handover_${taskId}_${seqStr}.yaml`),
    content,
  );
}

withDefaultEngineTransportCredential();

test.describe("delivery summary panel", () => {
  let taskId = "";
  let workspacesRoot = "";
  let deliveryFile = "";

  test.beforeAll(async ({ request, e2eRoot }) => {
    workspacesRoot = path.join(e2eRoot, "workspaces");
    deliveryFile = path.join(e2eRoot, "gh-delivery.json");
    fs.writeFileSync(deliveryFile, CANNED_DELIVERY);

    const res = await request.post("/api/workspaces", {
      data: {
        title: `e2e-delivery-${String(Date.now())}`,
        description: "e2e seed for delivery summary panel",
        budget: "medium",
      },
    });
    expect(res.ok()).toBeTruthy();
    ({ task_id: taskId } = (await res.json()) as { task_id: string });

    const handoverDir = path.join(workspacesRoot, taskId, "tasks", taskId, "handovers");
    fs.mkdirSync(handoverDir, { recursive: true });

    seedHandover(handoverDir, taskId, 1, {
      from_team: "implementation_plan",
    });
    seedHandover(handoverDir, taskId, 2, {
      from_team: "pr_merge",
      pr_number: 999,
    });
    seedHandover(handoverDir, taskId, 3, {
      from_team: "retrospective",
      full_synthesis: SYNTHESIS,
    });

    const metaPath = path.join(workspacesRoot, taskId, "tasks", taskId, "meta.json");
    const meta = JSON.parse(
      fs.readFileSync(metaPath, "utf-8"),
    ) as Record<string, unknown>;
    meta.status = "completed";
    fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));
  });

  test.afterAll(() => {
    if (taskId !== "") {
      fs.rmSync(path.join(workspacesRoot, taskId), {
        recursive: true,
        force: true,
      });
    }
    if (deliveryFile !== "") {
      fs.rmSync(deliveryFile, { force: true });
    }
  });

  test(
    "renders all delivery summary sections for a completed workspace @no-claude",
    async ({ page }) => {
      await page.goto(`/workspaces/${taskId}`);

      await expect(page.getByTestId("delivery-summary-panel")).toBeVisible();

      const delivery = page.getByTestId("situation-delivery");
      await expect(
        delivery.getByRole("link", { name: "feat: delivery summary panel" }),
      ).toHaveAttribute("href", "https://github.com/DejvMatikar/alan/pull/999");
      await expect(delivery.getByTestId("situation-files-changed")).toHaveText(
        "1 file changed",
      );
      await expect(delivery.getByTestId("situation-commits")).toContainText(
        "feat: add delivery summary panel",
      );

      const retro = page.getByTestId("retrospective");
      await expect(retro).toContainText("Things went smoothly");
      await expect(retro).toContainText("Some friction");
      await expect(retro).toContainText("Verification step alpha");

      const deferredItems = page.getByTestId("deferred-items");
      await expect(deferredItems.getByRole("listitem")).toHaveCount(2);
      const item0 = page.getByTestId("deferred-item-0");
      const item1 = page.getByTestId("deferred-item-1");
      await expect(item0).toBeVisible();
      await expect(item0).toHaveText(
        "[HIGH] Change: Refactor X | Where: alan/foo.py | Effort: medium",
      );
      await expect(item1).toBeVisible();
      await expect(item1).toHaveText(
        "[MED] Change: Add Y test | Where: tests/ | Effort: low",
      );
      await expect(item0.locator("a")).toHaveCount(0);
    },
  );
});
