import type { WorkflowTask } from "../../api";

export function makeTask(id: string, overrides: Partial<WorkflowTask> = {}): WorkflowTask {
  return {
    id,
    type: "io.alan.LLMStep",
    role: id,
    model: "sonnet",
    depends_on: [],
    status: "success",
    started_at: "2026-05-12T08:00:00Z",
    completed_at: "2026-05-12T08:00:10Z",
    stream_path: `agents/${id}.stream.jsonl`,
    dispatched: [],
    error: null,
    input_tokens: null,
    output_tokens: null,
    cache_creation_input_tokens: null,
    cache_read_input_tokens: null,
    total_cost_usd: null,
    usage_raw: null,
    ...overrides,
  };
}
