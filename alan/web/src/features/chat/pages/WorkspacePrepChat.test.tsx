import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import WorkspacePrepChat from "./WorkspacePrepChat";

function renderAt(taskId = "t1") {
  return render(
    <MemoryRouter initialEntries={[`/workspaces/${taskId}/prep-chat`]}>
      <Routes>
        <Route path="/workspaces/:taskId/prep-chat" element={<WorkspacePrepChat />} />
        <Route path="/workspaces/:taskId" element={<div data-testid="workspace-page">ws</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function buildTranscript(overrides: Record<string, unknown> = {}) {
  return {
    session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    kind: "build",
    consultant_key: null,
    title: "Add the widget",
    created_at: "2026-05-12T08:00:00Z",
    messages: [
      { role: "user", content: "build a widget", ts: "2026-05-12T08:00:00Z" },
      { role: "assistant", content: "sure, here's a plan", ts: "2026-05-12T08:00:05Z" },
    ],
    ...overrides,
  };
}

describe("WorkspacePrepChat", () => {
  it("renders nothing but a placeholder when taskId is missing", () => {
    render(
      <MemoryRouter initialEntries={["/workspaces/prep-chat"]}>
        <Routes>
          <Route path="/workspaces/:taskId?/prep-chat" element={<WorkspacePrepChat />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("missing-task")).toBeInTheDocument();
  });

  it("shows a loading state before the transcript resolves", () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () => new Promise(() => undefined)),
    );
    renderAt();
    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  it("renders the human user/assistant exchange via ChatTranscript", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () => HttpResponse.json(buildTranscript())),
    );
    renderAt();
    expect(await screen.findByTestId("chat-transcript")).toBeInTheDocument();
    expect(screen.getByTestId("chat-user")).toHaveTextContent("build a widget");
    expect(screen.getByTestId("chat-assistant")).toHaveTextContent("sure, here's a plan");
  });

  it("exposes 'Archived — read-only' as a role=status line", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () => HttpResponse.json(buildTranscript())),
    );
    renderAt();
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Archived — read-only");
  });

  it("names the conversation kind for a build chat", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () => HttpResponse.json(buildTranscript())),
    );
    renderAt();
    expect(await screen.findByTestId("prep-chat-kind")).toHaveTextContent("Build conversation");
  });

  it("names the conversation kind and consultant for an explore chat with a known consultant", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json(buildTranscript({ kind: "explore", consultant_key: "brainstorming" })),
      ),
    );
    renderAt();
    expect(await screen.findByText("Explore conversation — Brainstorming")).toBeInTheDocument();
  });

  it("falls back to a generic label when the consultant key is missing", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json(buildTranscript({ kind: "explore", consultant_key: null })),
      ),
    );
    renderAt();
    expect(await screen.findByTestId("prep-chat-kind")).toHaveTextContent(
      "Explore conversation — unknown consultant",
    );
  });

  it("falls back to the raw consultant key when unknown", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json(buildTranscript({ kind: "explore", consultant_key: "mystery_key" })),
      ),
    );
    renderAt();
    expect(await screen.findByTestId("prep-chat-kind")).toHaveTextContent(
      "Explore conversation — mystery_key",
    );
  });

  it("offers a labelled way back to the owning workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () => HttpResponse.json(buildTranscript())),
    );
    renderAt();
    const link = await screen.findByRole("link", { name: /Back to workspace/ });
    expect(link).toHaveAttribute("href", "/workspaces/t1");
  });

  it("shows an inline alert and a way back when the fetch fails", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json({ detail: "no archived preparation chat" }, { status: 404 }),
      ),
    );
    renderAt();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("no archived preparation chat");
    expect(screen.getByRole("link", { name: /Back to workspace/ })).toHaveAttribute(
      "href",
      "/workspaces/t1",
    );
  });

  it("ignores a successful fetch that resolves after unmount", async () => {
    let resolve: ((value: Response) => void) | null = null;
    server.use(
      http.get(
        "/api/workspaces/t1/prep-chat",
        () =>
          new Promise<Response>((res) => {
            resolve = res;
          }),
      ),
    );
    const { unmount } = renderAt();
    await new Promise((r) => setTimeout(r, 5));
    unmount();
    resolve!(HttpResponse.json(buildTranscript()));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("ignores a fetch rejection that resolves after unmount", async () => {
    let reject: ((reason?: unknown) => void) | null = null;
    server.use(
      http.get(
        "/api/workspaces/t1/prep-chat",
        () =>
          new Promise<Response>((_res, rej) => {
            reject = rej;
          }),
      ),
    );
    const { unmount } = renderAt();
    await new Promise((r) => setTimeout(r, 5));
    unmount();
    reject!(new Error("late failure"));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("clears a stale load error when taskId changes to one that resolves", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json({ detail: "no archived preparation chat" }, { status: 404 }),
      ),
      http.get("/api/workspaces/t2/prep-chat", () => HttpResponse.json(buildTranscript())),
    );
    render(
      <MemoryRouter initialEntries={["/workspaces/t1/prep-chat"]}>
        <Routes>
          <Route
            path="/workspaces/:taskId/prep-chat"
            element={
              <>
                <Link to="/workspaces/t2/prep-chat">go to t2</Link>
                <WorkspacePrepChat />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("no archived preparation chat");
    fireEvent.click(screen.getByRole("link", { name: "go to t2" }));
    expect(await screen.findByTestId("chat-user")).toHaveTextContent("build a widget");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows loading instead of stale content when taskId changes to a slower request", async () => {
    let resolveT2: ((value: Response) => void) | null = null;
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () => HttpResponse.json(buildTranscript())),
      http.get(
        "/api/workspaces/t2/prep-chat",
        () =>
          new Promise<Response>((res) => {
            resolveT2 = res;
          }),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/workspaces/t1/prep-chat"]}>
        <Routes>
          <Route
            path="/workspaces/:taskId/prep-chat"
            element={
              <>
                <Link to="/workspaces/t2/prep-chat">go to t2</Link>
                <WorkspacePrepChat />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("chat-user")).toHaveTextContent("build a widget");
    fireEvent.click(screen.getByRole("link", { name: "go to t2" }));
    expect(await screen.findByTestId("loading")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-user")).not.toBeInTheDocument();
    resolveT2!(
      HttpResponse.json(
        buildTranscript({
          messages: [{ role: "user", content: "second workspace", ts: "2026-05-12T09:00:00Z" }],
        }),
      ),
    );
    expect(await screen.findByTestId("chat-user")).toHaveTextContent("second workspace");
  });

  it("does not render a composer or the tabbed chat scaffold", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () => HttpResponse.json(buildTranscript())),
    );
    renderAt();
    await screen.findByTestId("chat-transcript");
    expect(screen.queryByLabelText("Message")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("renders the archived brief under a Summary section when the archive carries one", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json(
          buildTranscript({
            brief: { content: "## Deferred\n\nWrite tests next", updated_at: "2026-05-12T09:00:00Z" },
          }),
        ),
      ),
    );
    renderAt();
    const summary = await screen.findByRole("region", { name: "Summary" });
    expect(summary).toHaveTextContent("Deferred");
    expect(summary).toHaveTextContent("Write tests next");
  });

  it("keeps the ActivityLine disclosure on its pre-existing bounded overlay geometry — out of this item's scope", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json(
          buildTranscript({
            messages: [
              {
                role: "assistant",
                content: "sure, here's a plan",
                ts: "2026-05-12T08:00:05Z",
                operations: ["Read: a.py", "Bash: ls"],
              },
            ],
          }),
        ),
      ),
    );
    renderAt();
    const toggle = await screen.findByTestId("activity-line-toggle");
    await user.click(toggle);
    const list = screen.getByTestId("activity-line-list");
    expect(list.className).toContain("absolute");
    expect(list.className).toContain("max-h-40");
    expect(list.className).toContain("overflow-y-auto");
    expect(screen.getByTestId("activity-line").className).toContain(
      "max-w-[min(80%,65ch)]",
    );
  });

  it("renders no Summary section when the archive has no brief", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () => HttpResponse.json(buildTranscript())),
    );
    renderAt();
    await screen.findByTestId("chat-transcript");
    expect(screen.queryByRole("region", { name: "Summary" })).not.toBeInTheDocument();
  });
});
