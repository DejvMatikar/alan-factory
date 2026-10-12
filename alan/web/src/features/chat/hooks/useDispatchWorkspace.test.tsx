import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { CounterCase } from "../../../api";
import { server } from "../../../test/server";
import { useDispatchWorkspace } from "./useDispatchWorkspace";

function makeWrapper() {
  return function Wrapper({ children }: Readonly<{ children: React.ReactNode }>) {
    return (
      <MemoryRouter initialEntries={["/build"]}>
        <Routes>
          <Route path="*" element={<>{children}</>} />
        </Routes>
      </MemoryRouter>
    );
  };
}

const COUNTER_CASE: CounterCase = {
  against: "scope is still shifting under discussion",
  cheapest_80: "ship the read path only, defer writes",
  cost_estimate: "insufficient data",
};

describe("useDispatchWorkspace", () => {
  it("issues a single fused create-and-dispatch call carrying counter_case, so the backend can log the waiver", async () => {
    let calls = 0;
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        calls += 1;
        createBody = await request.json();
        return HttpResponse.json({ task_id: "ws-1", root_task_ids: ["ws-1"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("implementation_plan", "build"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description", { counterCase: COUNTER_CASE });
    await waitFor(() => expect(calls).toBe(1));
    expect(createBody).toEqual({
      team: "implementation_plan",
      counter_case: COUNTER_CASE,
      title: "Title",
      description: "Description",
    });
  });

  it("omits counter_case from the fused call when the caller doesn't pass one", async () => {
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ task_id: "ws-2", root_task_ids: ["ws-2"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("implementation_plan", "build"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description");
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toEqual({
      team: "implementation_plan",
      title: "Title",
      description: "Description",
    });
  });

  it("forwards instructions on the fused call when the caller passes them", async () => {
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ task_id: "ws-3", root_task_ids: ["ws-3"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("implementation_plan", "build"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description", {
      counterCase: COUNTER_CASE,
      instructions: "Focus on the read path only.",
    });
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toEqual({
      team: "implementation_plan",
      counter_case: COUNTER_CASE,
      instructions: "Focus on the read path only.",
      title: "Title",
      description: "Description",
    });
  });

  it("forwards projectId on the fused call when the caller passes it", async () => {
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ task_id: "ws-4", root_task_ids: ["ws-4"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("brainstorming", "explore"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description", { projectId: "proj-1" });
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toEqual({
      team: "brainstorming",
      title: "Title",
      description: "Description",
      project_id: "proj-1",
    });
  });

  it("forwards roadmapItemId on the fused call when the caller passes it, alongside projectId", async () => {
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ task_id: "ws-5", root_task_ids: ["ws-5"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("brainstorming", "explore"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description", {
      projectId: "proj-1",
      roadmapItemId: "itm_1",
    });
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toEqual({
      team: "brainstorming",
      title: "Title",
      description: "Description",
      project_id: "proj-1",
      roadmap_item_id: "itm_1",
    });
  });

  it("omits roadmap_item_id from the fused call when the caller doesn't pass one", async () => {
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ task_id: "ws-6", root_task_ids: ["ws-6"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("brainstorming", "explore"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description");
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toEqual({
      team: "brainstorming",
      title: "Title",
      description: "Description",
    });
  });

  it("forwards prep_chat_kind and prep_chat_session_id together when the caller passes a live session id", async () => {
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ task_id: "ws-7", root_task_ids: ["ws-7"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("implementation_plan", "build"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description", { prepChatSessionId: "sess-1" });
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toEqual({
      team: "implementation_plan",
      title: "Title",
      description: "Description",
      prep_chat_kind: "build",
      prep_chat_session_id: "sess-1",
    });
  });

  it("omits prep_chat_kind and prep_chat_session_id when the caller passes no session id, so a session-less dispatch never 422s on the backend's together-or-neither validator", async () => {
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ task_id: "ws-8", root_task_ids: ["ws-8"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("brainstorming", "explore"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description");
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).not.toHaveProperty("prep_chat_kind");
    expect(createBody).not.toHaveProperty("prep_chat_session_id");
  });

  it("propagates a fused-call failure without navigating", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ detail: "team not admitted" }, { status: 422 }),
      ),
    );
    const { result } = renderHook(
      () => useDispatchWorkspace("implementation_plan", "build"),
      { wrapper: makeWrapper() },
    );
    await result.current.dispatchWorkspace("Title", "Description");
    await waitFor(() => expect(result.current.dispatchError).not.toBeNull());
    expect(result.current.dispatchError).toContain("team not admitted");
  });
});
