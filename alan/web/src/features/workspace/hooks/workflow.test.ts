import { describe, expect, it } from "vitest";

import type { WorkflowTask } from "../../../api";
import {
  ROW_HEIGHT,
  computeLayout,
  mergeNodeUpdate,
  parseStreamPayload,
  parseTaskFromEvent,
  resolveStepLabel,
  stepDisplayName,
} from "./workflow";

function makeTask(
  id: string,
  overrides: Partial<WorkflowTask> = {},
): WorkflowTask {
  const base: WorkflowTask = {
    id,
    type: "io.alan.LLMStep",
    role: id,
    model: "sonnet",
    depends_on: [],
    status: "running",
    started_at: "2026-05-12T08:00:00Z",
    completed_at: null,
    stream_path: `agents/${id}.stream.jsonl`,
    dispatched: [],
    error: null,
    input_tokens: null,
    output_tokens: null,
    cache_creation_input_tokens: null,
    cache_read_input_tokens: null,
    total_cost_usd: null,
    usage_raw: null,
  };
  return { ...base, ...overrides };
}

describe("computeLayout", () => {
  it("places independent tasks at level 0", () => {
    const layout = computeLayout([makeTask("a"), makeTask("b")]);
    expect(layout.map((l) => ({ id: l.task.id, x: l.x, y: l.y }))).toEqual([
      { id: "a", x: 0, y: 0 },
      { id: "b", x: 0, y: ROW_HEIGHT },
    ]);
  });

  it("places dependents one column right", () => {
    const layout = computeLayout([
      makeTask("orch"),
      makeTask("w1", { depends_on: ["orch"] }),
      makeTask("w2", { depends_on: ["orch"] }),
      makeTask("synth", { depends_on: ["w1", "w2"] }),
    ]);
    const byId = Object.fromEntries(layout.map((l) => [l.task.id, l]));
    expect(byId.orch!.x).toBe(0);
    expect(byId.w1!.x).toBe(260);
    expect(byId.w2!.x).toBe(260);
    expect(byId.synth!.x).toBe(520);
  });

  it("returns empty for empty input", () => {
    expect(computeLayout([])).toEqual([]);
  });

  it("treats unknown dependency as level 0", () => {
    const layout = computeLayout([makeTask("a", { depends_on: ["missing"] })]);
    expect(layout[0]!.x).toBe(260);
  });
});

describe("stepDisplayName", () => {
  it("returns the role alone when it equals the id", () => {
    expect(stepDisplayName(makeTask("reviewer"))).toBe("reviewer");
  });

  it("appends the id in parentheses when it differs from the role", () => {
    expect(stepDisplayName(makeTask("step_2", { role: "reviewer" }))).toBe("reviewer (step_2)");
  });
});

describe("resolveStepLabel", () => {
  it("resolves the display name of the task with the given id", () => {
    const tasks = [makeTask("a"), makeTask("step_2", { role: "reviewer" })];
    expect(resolveStepLabel(tasks, "step_2")).toBe("reviewer (step_2)");
  });

  it("falls back to the node id when no task matches", () => {
    expect(resolveStepLabel([makeTask("a")], "gone")).toBe("gone");
  });
});

describe("mergeNodeUpdate", () => {
  it("appends a new node", () => {
    const next = mergeNodeUpdate([makeTask("a")], makeTask("b"));
    expect(next.map((t) => t.id)).toEqual(["a", "b"]);
  });

  it("replaces an existing node by id", () => {
    const next = mergeNodeUpdate(
      [makeTask("a"), makeTask("b")],
      makeTask("b", { status: "success" }),
    );
    expect(next[1]!.status).toBe("success");
  });

  it("preserves order when replacing", () => {
    const next = mergeNodeUpdate(
      [makeTask("a"), makeTask("b"), makeTask("c")],
      makeTask("b", { status: "failed" }),
    );
    expect(next.map((t) => t.id)).toEqual(["a", "b", "c"]);
  });
});

describe("parseTaskFromEvent", () => {
  it("returns invId + task when the wrapper has both", () => {
    const node = JSON.stringify(makeTask("a", { status: "running" }));
    const evt = JSON.stringify({ inv_id: "inv_001", role: "a", node });
    const result = parseTaskFromEvent(evt);
    expect(result?.invId).toBe("inv_001");
    expect(result?.task.id).toBe("a");
    expect(result?.task.status).toBe("running");
  });

  it("returns null when data is not JSON", () => {
    expect(parseTaskFromEvent("not json")).toBeNull();
  });

  it("returns null when data is an array", () => {
    expect(parseTaskFromEvent("[1]")).toBeNull();
  });

  it("returns null when inv_id is missing", () => {
    const node = JSON.stringify(makeTask("a"));
    expect(parseTaskFromEvent(JSON.stringify({ role: "a", node }))).toBeNull();
  });

  it("returns null when inv_id is empty", () => {
    const node = JSON.stringify(makeTask("a"));
    expect(
      parseTaskFromEvent(JSON.stringify({ inv_id: "", role: "a", node })),
    ).toBeNull();
  });

  it("returns null when inv_id is wrong type", () => {
    const node = JSON.stringify(makeTask("a"));
    expect(
      parseTaskFromEvent(JSON.stringify({ inv_id: 1, role: "a", node })),
    ).toBeNull();
  });

  it("returns null when node is missing or wrong type", () => {
    expect(
      parseTaskFromEvent(JSON.stringify({ inv_id: "inv_001", role: "a" })),
    ).toBeNull();
    expect(
      parseTaskFromEvent(
        JSON.stringify({ inv_id: "inv_001", role: "a", node: 42 }),
      ),
    ).toBeNull();
  });

  it("returns null when node JSON is invalid", () => {
    expect(
      parseTaskFromEvent(
        JSON.stringify({ inv_id: "inv_001", role: "a", node: "not json" }),
      ),
    ).toBeNull();
  });

  it("returns null when node is array", () => {
    expect(
      parseTaskFromEvent(
        JSON.stringify({ inv_id: "inv_001", role: "a", node: "[]" }),
      ),
    ).toBeNull();
  });

  it("returns null when node lacks required fields", () => {
    const bad = JSON.stringify({
      inv_id: "inv_001",
      role: "a",
      node: JSON.stringify({ id: 1 }),
    });
    expect(parseTaskFromEvent(bad)).toBeNull();
  });
});

describe("parseStreamPayload", () => {
  it("returns invId + role + event when all are valid", () => {
    const inner = { type: "result", result: "ok" };
    const wrapper = JSON.stringify({
      inv_id: "inv_002",
      role: "alpha",
      event: JSON.stringify(inner),
    });
    const out = parseStreamPayload(wrapper);
    expect(out).toEqual({ invId: "inv_002", role: "alpha", event: inner });
  });

  it("returns null for non-JSON data", () => {
    expect(parseStreamPayload("not json")).toBeNull();
  });

  it("returns null for non-object data", () => {
    expect(parseStreamPayload("[1, 2]")).toBeNull();
  });

  it("returns null when inv_id is missing", () => {
    expect(
      parseStreamPayload(JSON.stringify({ role: "a", event: "{}" })),
    ).toBeNull();
  });

  it("returns null when inv_id is empty", () => {
    expect(
      parseStreamPayload(
        JSON.stringify({ inv_id: "", role: "a", event: "{}" }),
      ),
    ).toBeNull();
  });

  it("returns null when inv_id is wrong type", () => {
    expect(
      parseStreamPayload(
        JSON.stringify({ inv_id: 1, role: "a", event: "{}" }),
      ),
    ).toBeNull();
  });

  it("returns null when role is not string", () => {
    expect(
      parseStreamPayload(
        JSON.stringify({ inv_id: "inv_001", role: 1, event: "{}" }),
      ),
    ).toBeNull();
  });

  it("returns null when event is not string", () => {
    expect(
      parseStreamPayload(
        JSON.stringify({ inv_id: "inv_001", role: "a", event: 1 }),
      ),
    ).toBeNull();
  });

  it("returns null when event JSON is invalid", () => {
    expect(
      parseStreamPayload(
        JSON.stringify({ inv_id: "inv_001", role: "a", event: "not json" }),
      ),
    ).toBeNull();
  });

  it("returns null when event is array", () => {
    expect(
      parseStreamPayload(
        JSON.stringify({ inv_id: "inv_001", role: "a", event: "[]" }),
      ),
    ).toBeNull();
  });
});
