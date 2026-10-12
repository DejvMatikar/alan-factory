import { fireEvent, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { STUB_DEFAULT_ENGINE_KEY } from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import { useWorkspaceLifecycle } from "../hooks/useWorkspaceLifecycle";
import WorkspaceHeader from "./WorkspaceHeader";

function Harness({ ws }: Readonly<{ ws: ReturnType<typeof makeWorkspaceDetail> }>) {
  const lifecycle = useWorkspaceLifecycle("t1", ws, vi.fn());
  return <WorkspaceHeader ws={ws} taskId="t1" lifecycle={lifecycle} />;
}

function renderHeader(ws: ReturnType<typeof makeWorkspaceDetail>) {
  return render(
    <MemoryRouter initialEntries={["/workspaces/t1"]}>
      <Harness ws={ws} />
    </MemoryRouter>,
  );
}

function stubNonDefaultEngine() {
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: [
          { repo: { owner: "DejvMatikar", repo: "alan" } },
          { repo: { owner: "acme", repo: "sprocket" } },
        ],
      }),
    ),
  );
}

describe("WorkspaceHeader", () => {
  it("renders a page-title h1 and status badge", () => {
    renderHeader(makeWorkspaceDetail({ title: "My Workspace" }));
    expect(screen.getByRole("heading", { level: 1, name: "My Workspace" })).toBeInTheDocument();
    expect(screen.getByTestId("status-active")).toBeInTheDocument();
  });

  it("falls back to task_id when title is blank", () => {
    renderHeader(makeWorkspaceDetail({ title: "", task_id: "t1" }));
    expect(screen.getByRole("heading", { level: 1, name: "t1" })).toBeInTheDocument();
  });

  it("links the back link to the workspace's own repository dashboard", () => {
    renderHeader(makeWorkspaceDetail({ engine_key: "acme/sprocket" }));
    const back = screen.getByTestId("workspace-back-link");
    expect(back).toHaveAttribute("href", "/repos/acme/sprocket");
    expect(back).toHaveTextContent("acme/sprocket");
  });

  it("falls back to the listed default for the back link when engine_key is absent", async () => {
    renderHeader(makeWorkspaceDetail({ engine_key: null }));
    expect(await screen.findByTestId("workspace-back-link")).toHaveAttribute(
      "href",
      `/repos/${STUB_DEFAULT_ENGINE_KEY}`,
    );
  });

  it("omits the PR link when pr_number is null", () => {
    renderHeader(makeWorkspaceDetail({ pr_number: null }));
    expect(screen.queryByTestId("workspace-pr-link")).not.toBeInTheDocument();
  });

  it("renders the PR number as a link when present", () => {
    renderHeader(makeWorkspaceDetail({ pr_number: 42, engine_key: "acme/sprocket" }));
    const link = screen.getByTestId("workspace-pr-link");
    expect(link).toHaveTextContent("PR #42");
    expect(link).toHaveAttribute("href", "https://github.com/acme/sprocket/pull/42");
  });

  it("renders a degraded, non-clickable PR reference when engine_key is absent", () => {
    renderHeader(makeWorkspaceDetail({ pr_number: 42, engine_key: null }));
    const el = screen.getByTestId("workspace-pr-link");
    expect(el.tagName).toBe("SPAN");
  });

  it("omits 'live since' before delivery", () => {
    renderHeader(makeWorkspaceDetail({ live_at: null }));
    expect(screen.queryByTestId("workspace-live-since")).not.toBeInTheDocument();
  });

  it("shows 'live since' once delivered", () => {
    renderHeader(makeWorkspaceDetail({ live_at: "2026-05-12T08:00:00Z" }));
    expect(screen.getByTestId("workspace-live-since")).toHaveTextContent(/live since/);
  });

  it("renders no engine badge when only the default engine exists", () => {
    renderHeader(makeWorkspaceDetail({ engine_key: "acme/sprocket" }));
    expect(screen.queryByTestId("engine-badge-t1")).not.toBeInTheDocument();
  });

  it("renders an engine badge beside the status badge once a second repo exists", async () => {
    stubNonDefaultEngine();
    renderHeader(makeWorkspaceDetail({ engine_key: "acme/sprocket" }));
    expect(await screen.findByTestId("engine-badge-t1")).toHaveTextContent("acme/sprocket");
  });

  it("carries no solid-filled button in the header actions", () => {
    renderHeader(makeWorkspaceDetail({ status: "active", has_running_job: true }));
    for (const testId of ["stop-button", "archive-button", "delete-button"]) {
      const el = screen.getByTestId(testId);
      expect(el.className).not.toMatch(/bg-btn-(action|create|destroy|reset) /);
    }
  });

  it("opens the delete confirmation dialog from the header", async () => {
    renderHeader(makeWorkspaceDetail());
    fireEvent.click(screen.getByTestId("delete-button"));
    expect(await screen.findByTestId("delete-dialog")).toBeInTheDocument();
  });
});

describe("WorkspaceHeader — the preset operator link", () => {
  it("links to the operator terminal scoped to this workspace when the server reports operator_link", () => {
    renderHeader(makeWorkspaceDetail({ task_id: "fix-the-thing", operator_link: true }));
    const link = screen.getByTestId("workspace-operator-link");
    expect(link).toHaveAttribute("href", "/operator?scope=workspace&target=fix-the-thing");
    expect(link).toHaveTextContent("Open in operator terminal");
  });

  it("shows no link when the server does not report operator_link", () => {
    renderHeader(makeWorkspaceDetail({ operator_link: false }));
    expect(screen.queryByTestId("workspace-operator-link")).not.toBeInTheDocument();
  });

  it("follows operator_link and never the status the client sees", () => {
    const stuckLooking = renderHeader(
      makeWorkspaceDetail({ status: "escalated", operator_link: false }),
    );
    expect(screen.queryByTestId("workspace-operator-link")).not.toBeInTheDocument();
    stuckLooking.unmount();
    renderHeader(makeWorkspaceDetail({ status: "active", operator_link: true }));
    expect(screen.getByTestId("workspace-operator-link")).toBeInTheDocument();
  });

  it("percent-encodes the task id it puts in the link", () => {
    renderHeader(makeWorkspaceDetail({ task_id: "a b/c", operator_link: true }));
    expect(screen.getByTestId("workspace-operator-link")).toHaveAttribute(
      "href",
      "/operator?scope=workspace&target=a%20b%2Fc",
    );
  });

  it("meets the 44px minimum tap-target height", () => {
    renderHeader(makeWorkspaceDetail({ operator_link: true }));
    expect(screen.getByTestId("workspace-operator-link").className).toContain("min-h-11");
  });
});
