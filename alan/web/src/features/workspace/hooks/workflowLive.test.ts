import { renderHook } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { useLiveWorkspace } from "./workflowLive";
import { NOTICES_CAP } from "./workspaceNotices";
import { server } from "../../../test/server";

describe("useLiveWorkspace WORKSPACE_LIFECYCLE_KINDS", () => {
  it("calls onWorkspaceChanged when a workspace_unarchived SSE event arrives", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse('event: workspace_unarchived\ndata: {"task_id":"t1"}\n\n', {
            headers: { "Content-Type": "text/event-stream" },
          }),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t1", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });

  it("calls onWorkspaceChanged when a job_retry_scheduled SSE event arrives", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: job_retry_scheduled\ndata: {"task_id":"t1","attempt":"1","eligible_at":"1234567890.0"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t1", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });

  it("calls onWorkspaceChanged when an injection_overflow SSE event arrives", async () => {
    server.use(
      http.get(
        "/api/workspaces/t2/events",
        () =>
          new HttpResponse(
            'event: injection_overflow\ndata: {"dropped_count":"2","task_id":"t2"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t2", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });

  it("calls onWorkspaceChanged when a manager_summary_ready SSE event arrives", async () => {
    server.use(
      http.get(
        "/api/workspaces/t3/events",
        () =>
          new HttpResponse(
            'event: manager_summary_ready\ndata: {"task_id":"t3"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t3", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });

  it("calls onWorkspaceChanged when a pr_merge_rederived SSE event arrives", async () => {
    server.use(
      http.get(
        "/api/workspaces/t4/events",
        () =>
          new HttpResponse(
            'event: pr_merge_rederived\ndata: {"task_id":"t4","inv_id":"inv_002","team":"pr_merge"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t4", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });

  it("calls onWorkspaceChanged when a frozen_chain_park SSE event arrives", async () => {
    server.use(
      http.get(
        "/api/workspaces/t6/events",
        () =>
          new HttpResponse(
            'event: frozen_chain_park\ndata: {"reason":"frozen chain: ancestor task \'a1\' exhausted the revisit cap with 2 descendant(s) permanently blocked","workspace_id":"t6","ancestor_id":"a1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t6", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });

  it("calls onWorkspaceChanged when a deploy_verify_rederived SSE event arrives", async () => {
    server.use(
      http.get(
        "/api/workspaces/t5/events",
        () =>
          new HttpResponse(
            'event: deploy_verify_rederived\ndata: {"task_id":"t5","inv_id":"inv_003","team":"deploy_verify"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t5", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });
});

describe("useLiveWorkspace slot_work_preserved and slot_work_rescued", () => {
  it("produces an info notice for slot_work_preserved", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: slot_work_preserved\ndata: {"n_commits":"3","task_id":"t1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    expect(result.current.notices[0]?.severity).toBe("info");
    expect(result.current.notices[0]?.text).toContain("3 commit(s)");
  });

  it("produces a warn notice for slot_work_rescued", async () => {
    server.use(
      http.get(
        "/api/workspaces/t2/events",
        () =>
          new HttpResponse(
            'event: slot_work_rescued\ndata: {"rescue_ref":"rescue/t@inv_001","task_id":"t2"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t2", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    expect(result.current.notices[0]?.severity).toBe("warn");
    expect(result.current.notices[0]?.text).toContain("rescue/t@inv_001");
  });

  it("calls onWorkspaceChanged for slot_work_preserved", async () => {
    server.use(
      http.get(
        "/api/workspaces/t3/events",
        () =>
          new HttpResponse(
            'event: slot_work_preserved\ndata: {"n_commits":"1","task_id":"t3"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t3", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });

  it("calls onWorkspaceChanged for slot_work_rescued", async () => {
    server.use(
      http.get(
        "/api/workspaces/t4/events",
        () =>
          new HttpResponse(
            'event: slot_work_rescued\ndata: {"rescue_ref":"rescue/t@inv_001","task_id":"t4"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t4", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });

  it("produces a warn notice for slot_resume_waiting", async () => {
    server.use(
      http.get(
        "/api/workspaces/t5/events",
        () =>
          new HttpResponse(
            'event: slot_resume_waiting\ndata: {"task_id":"t5","inv_id":"inv_001","slot_index":"2"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t5", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    expect(result.current.notices[0]?.severity).toBe("warn");
    expect(result.current.notices[0]?.text).toContain("2");
  });

  it("calls onWorkspaceChanged for slot_resume_waiting", async () => {
    server.use(
      http.get(
        "/api/workspaces/t6/events",
        () =>
          new HttpResponse(
            'event: slot_resume_waiting\ndata: {"task_id":"t6","inv_id":"inv_001","slot_index":"1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    renderHook(() => useLiveWorkspace("t6", { onWorkspaceChanged }));
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
  });
});

describe("useLiveWorkspace dag_stall", () => {
  it("calls onWorkspaceChanged and produces a warn notice for dag_stall", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: dag_stall\ndata: {"reason":"no runnable tasks","workspace_id":"t1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    const { result } = renderHook(() =>
      useLiveWorkspace("t1", { onWorkspaceChanged }),
    );
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    expect(result.current.notices[0]?.severity).toBe("warn");
    expect(result.current.notices[0]?.text).toContain("no runnable tasks");
  });
});

describe("useLiveWorkspace frozen_chain_park", () => {
  it("calls onWorkspaceChanged and produces a warn notice for frozen_chain_park", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: frozen_chain_park\ndata: {"reason":"frozen chain: ancestor task \'a1\' exhausted the revisit cap with 2 descendant(s) permanently blocked","workspace_id":"t1","ancestor_id":"a1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    const { result } = renderHook(() =>
      useLiveWorkspace("t1", { onWorkspaceChanged }),
    );
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    expect(result.current.notices[0]?.severity).toBe("warn");
    expect(result.current.notices[0]?.text).toContain(
      "ancestor task 'a1' exhausted the revisit cap",
    );
  });
});

describe("useLiveWorkspace notices strand", () => {
  it("produces a notice for job_failed with error severity", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: job_failed\ndata: {"team":"research","job_id":"7"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    expect(result.current.notices[0]?.severity).toBe("error");
    expect(result.current.notices[0]?.text).toBe("Job failed");
  });

  it("does NOT produce a notice for router_enqueue (silent)", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: router_enqueue\ndata: {"task_id":"t1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    const { result } = renderHook(() =>
      useLiveWorkspace("t1", { onWorkspaceChanged }),
    );
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
    expect(result.current.notices).toHaveLength(0);
  });

  it("does NOT produce a notice for router_done (silent)", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: router_done\ndata: {"task_id":"t1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    const { result } = renderHook(() =>
      useLiveWorkspace("t1", { onWorkspaceChanged }),
    );
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
    expect(result.current.notices).toHaveLength(0);
  });

  it("does NOT produce a notice for events with malformed data", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            "event: job_failed\ndata: not-json\n\n",
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const onWorkspaceChanged = vi.fn();
    const { result } = renderHook(() =>
      useLiveWorkspace("t1", { onWorkspaceChanged }),
    );
    await vi.waitFor(() => {
      expect(onWorkspaceChanged).toHaveBeenCalledOnce();
    });
    expect(result.current.notices).toHaveLength(0);
  });

  it("accumulates multiple notices in order", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: job_started\ndata: {"job_id":"1"}\n\nevent: job_failed\ndata: {"job_id":"1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(2);
    });
    expect(result.current.notices[0]?.severity).toBe("info");
    expect(result.current.notices[1]?.severity).toBe("error");
  });

  it("clears notices when taskId changes", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: job_failed\ndata: {"job_id":"1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
      http.get("/api/workspaces/t2/events", () => HttpResponse.error()),
    );
    const { result, rerender } = renderHook(
      ({ taskId }: { taskId: string }) => useLiveWorkspace(taskId, {}),
      { initialProps: { taskId: "t1" } },
    );
    await vi.waitFor(() => {
      expect(result.current.notices.length).toBeGreaterThan(0);
    });
    rerender({ taskId: "t2" });
    expect(result.current.notices).toHaveLength(0);
  });

  it("caps notices at NOTICES_CAP dropping the oldest", async () => {
    const events = Array.from(
      { length: NOTICES_CAP + 1 },
      () => 'event: job_failed\ndata: {"x":"y"}\n\n',
    ).join("");
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(events, {
            headers: { "Content-Type": "text/event-stream" },
          }),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(NOTICES_CAP);
    });
  });

  it("sets stickyNotice for a stuck-class notice (dag_stall)", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: dag_stall\ndata: {"reason":"no runnable tasks","workspace_id":"t1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.stickyNotice).not.toBeNull();
    });
    expect(result.current.stickyNotice?.severity).toBe("warn");
  });

  it("sets stickyNotice for a stuck-class notice (frozen_chain_park)", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: frozen_chain_park\ndata: {"reason":"frozen chain: ancestor task \'a1\' exhausted the revisit cap with 2 descendant(s) permanently blocked","workspace_id":"t1","ancestor_id":"a1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.stickyNotice).not.toBeNull();
    });
    expect(result.current.stickyNotice?.severity).toBe("warn");
  });

  it("does not update stickyNotice for non-stuck-class warn notice (workspace_paused)", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: workspace_paused\ndata: {"task_id":"t1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    expect(result.current.stickyNotice).toBeNull();
  });

  it("does not update stickyNotice for info severity notices", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: job_completed\ndata: {"job_id":"1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    expect(result.current.stickyNotice).toBeNull();
  });

  it("clears stickyNotice when taskId changes", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: router_escalate\ndata: {"task_id":"t1","from_team":"impl","kind":"escalate","reason":"loop"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
      http.get("/api/workspaces/t2/events", () => HttpResponse.error()),
    );
    const { result, rerender } = renderHook(
      ({ taskId }: { taskId: string }) => useLiveWorkspace(taskId, {}),
      { initialProps: { taskId: "t1" } },
    );
    await vi.waitFor(() => {
      expect(result.current.stickyNotice).not.toBeNull();
    });
    rerender({ taskId: "t2" });
    expect(result.current.stickyNotice).toBeNull();
  });

  it("each notice has an id, timestamp, text and severity", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: workspace_paused\ndata: {"task_id":"t1"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    const { result } = renderHook(() => useLiveWorkspace("t1", {}));
    await vi.waitFor(() => {
      expect(result.current.notices).toHaveLength(1);
    });
    const n = result.current.notices[0]!;
    expect(typeof n.id).toBe("string");
    expect(n.id.length).toBeGreaterThan(0);
    expect(typeof n.timestamp).toBe("string");
    expect(n.timestamp.length).toBeGreaterThan(0);
    expect(typeof n.text).toBe("string");
    expect(typeof n.severity).toBe("string");
  });
});
