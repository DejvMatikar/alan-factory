import { fireEvent, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  BASE_PROJECT,
  mockDoc,
  mockProject,
  renderAt,
  renderAtWithState,
  renderWithNav,
} from "../../../test/projectPageHarness";
import { server } from "../../../test/server";
import Project from "./Project";

describe("Project", () => {
  it("shows a loading state before the fetch resolves", () => {
    server.use(
      http.get("/api/projects/alpha", async () => {
        await new Promise(() => undefined);
        return HttpResponse.json(BASE_PROJECT);
      }),
    );
    renderAt();
    expect(screen.getByTestId("project-detail-loading")).toBeInTheDocument();
  });

  it("renders an error alert when the fetch fails (e.g. unknown id → 404)", async () => {
    server.use(
      http.get("/api/projects/missing", () =>
        HttpResponse.json({ detail: "project not found: missing" }, { status: 404 }),
      ),
    );
    mockDoc();
    renderAt("/projects/missing");
    const alert = await screen.findByTestId("project-detail-error");
    expect(alert).toHaveTextContent("404");
    expect(alert).toHaveTextContent("project not found: missing");
  });

  it("renders the title and status badge", async () => {
    mockProject();
    mockDoc();
    renderAt();
    expect(await screen.findByRole("heading", { level: 2, name: "Alpha" })).toBeInTheDocument();
    expect(screen.getByTestId("project-status-active")).toHaveTextContent("active");
  });

  it("renders no connect-unbound notice when arriving with no location state", async () => {
    mockProject();
    mockDoc();
    renderAt();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.queryByTestId("connect-unbound-notice")).not.toBeInTheDocument();
  });

  it("renders the connect-unbound notice when arriving with a connectUnboundReason", async () => {
    mockProject();
    mockDoc();
    renderAtWithState("/projects/alpha", { connectUnboundReason: "preflight_blocked" });
    const notice = await screen.findByTestId("connect-unbound-notice");
    expect(notice).toHaveTextContent(/currently blocked/);
  });

  it("renders created/updated dates", async () => {
    mockProject();
    mockDoc();
    renderAt();
    const dates = await screen.findByTestId("project-dates");
    expect(dates).toHaveTextContent(/Created/);
    expect(dates).toHaveTextContent(/Updated/);
  });

  it("does not render a doc_ref link when doc_ref is null", async () => {
    mockProject();
    mockDoc();
    renderAt();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.queryByTestId("project-doc-ref-link")).not.toBeInTheDocument();
  });

  it("renders doc_ref as a link pointing at the GitHub blob for a repo-relative path, without rendering its contents", async () => {
    mockProject({ doc_ref: "docs/roadmap.md", engine_key: "DejvMatikar/alan" });
    mockDoc();
    renderAt();
    const link = await screen.findByTestId("project-doc-ref-link");
    expect(link).toHaveTextContent("docs/roadmap.md");
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/DejvMatikar/alan/blob/main/docs/roadmap.md",
    );
    expect(link.innerHTML).toBe("docs/roadmap.md");
    expect(link).toHaveClass("text-btn-action", "underline");
    expect(link).not.toHaveClass("text-blue-600");
  });

  it("resolves a repo-relative doc_ref against a non-default engine's repo", async () => {
    mockProject({ doc_ref: "docs/roadmap.md", engine_key: "acme-corp/sprocket" });
    mockDoc();
    renderAt();
    const link = await screen.findByTestId("project-doc-ref-link");
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/acme-corp/sprocket/blob/main/docs/roadmap.md",
    );
  });

  it("renders a degraded, non-clickable doc_ref reference when the project has no engine_key", async () => {
    mockProject({ doc_ref: "docs/roadmap.md", engine_key: null });
    mockDoc();
    renderAt();
    const el = await screen.findByTestId("project-doc-ref-link");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveTextContent("docs/roadmap.md");
    expect(el).toHaveTextContent("repository unresolved");
    expect(el.className).toContain("bg-status-neutral-tint/15");
  });

  it("renders doc_ref as-is when it is already an absolute URL", async () => {
    mockProject({ doc_ref: "https://example.com/spec" });
    mockDoc();
    renderAt();
    const link = await screen.findByTestId("project-doc-ref-link");
    expect(link).toHaveAttribute("href", "https://example.com/spec");
  });

  it("renders a non-http(s) doc_ref (e.g. javascript:) as plain text, not a link", async () => {
    mockProject({ doc_ref: "javascript:alert(1)", engine_key: "DejvMatikar/alan" });
    mockDoc();
    renderAt();
    const el = await screen.findByTestId("project-doc-ref-link");
    expect(el.tagName).toBe("SPAN");
    expect(el).not.toHaveAttribute("href");
    expect(el).toHaveTextContent("javascript:alert(1)");
    expect(el).not.toHaveTextContent("repository unresolved");
  });

  it("renders a doc_ref that fails URL resolution as plain text, not a link", async () => {
    mockProject({ doc_ref: "http:[bad", engine_key: "DejvMatikar/alan" });
    mockDoc();
    renderAt();
    const el = await screen.findByTestId("project-doc-ref-link");
    expect(el.tagName).toBe("SPAN");
    expect(el).not.toHaveAttribute("href");
  });

  it("renders an honest empty state for the conversations section when no sessions are linked", async () => {
    mockProject();
    mockDoc();
    renderAt();
    expect(await screen.findByRole("heading", { name: "Conversations" })).toBeInTheDocument();
    expect(await screen.findByTestId("project-conversations-empty")).toHaveTextContent(
      "No conversations linked to this project yet.",
    );
  });

  it("renders an empty state for workspaces and pull requests when the ledger is empty", async () => {
    mockProject();
    mockDoc();
    renderAt();
    expect(await screen.findByTestId("project-workspaces-empty")).toHaveTextContent(
      "No workspaces yet.",
    );
    expect(screen.getByTestId("project-prs-empty")).toHaveTextContent("No pull requests yet.");
  });

  it("gathers workspaces-with-status and PR numbers from the ledger in one place", async () => {
    mockProject({
      engine_key: "DejvMatikar/alan",
      workspaces: [
        { task_id: "ws-a", title: "Workspace A", status: "completed", pr_number: 42 },
        { task_id: "ws-b", title: "Workspace B", status: "active", pr_number: null },
      ],
    });
    mockDoc();
    renderAt();
    const wsList = await screen.findByTestId("project-workspaces-list");
    expect(wsList).toHaveTextContent("Workspace A");
    expect(wsList).toHaveTextContent("Workspace B");
    expect(screen.getByTestId("status-completed")).toBeInTheDocument();
    expect(screen.getByTestId("status-active")).toBeInTheDocument();

    const prLink = screen.getByTestId("project-pr-link-ws-a");
    expect(prLink).toHaveAttribute("href", "https://github.com/DejvMatikar/alan/pull/42");
    expect(prLink).toHaveTextContent("PR #42");
    expect(prLink).toHaveClass("text-btn-action", "underline");
    expect(prLink).not.toHaveClass("text-blue-600");
    expect(screen.queryByTestId("project-pr-link-ws-b")).not.toBeInTheDocument();
  });

  it("resolves the PR list against a non-default engine's repo", async () => {
    mockProject({
      engine_key: "acme-corp/sprocket",
      workspaces: [{ task_id: "ws-a", title: "Workspace A", status: "completed", pr_number: 42 }],
    });
    mockDoc();
    renderAt();
    const prLink = await screen.findByTestId("project-pr-link-ws-a");
    expect(prLink).toHaveAttribute("href", "https://github.com/acme-corp/sprocket/pull/42");
  });

  it("renders a degraded, non-clickable PR reference in the PR list when the project has no engine_key", async () => {
    mockProject({
      engine_key: null,
      workspaces: [{ task_id: "ws-a", title: "Workspace A", status: "completed", pr_number: 42 }],
    });
    mockDoc();
    renderAt();
    const el = await screen.findByTestId("project-pr-link-ws-a");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveTextContent("PR #42");
    expect(el).toHaveTextContent("repository unresolved");
  });

  it("falls back to task_id when a ledger entry's title is empty", async () => {
    mockProject({
      workspaces: [{ task_id: "untitled-ws", title: "", status: "active", pr_number: null }],
    });
    mockDoc();
    renderAt();
    expect(await screen.findByRole("link", { name: "untitled-ws" })).toBeInTheDocument();
  });

  it("links a ledger workspace to its detail page", async () => {
    mockProject({
      workspaces: [{ task_id: "ws-a", title: "Workspace A", status: "active", pr_number: null }],
    });
    mockDoc();
    renderAt();
    const link = await screen.findByRole("link", { name: "Workspace A" });
    expect(link).toHaveAttribute("href", "/workspaces/ws-a");
  });

  it("archived project shows the archived status badge", async () => {
    mockProject({ status: "archived" });
    mockDoc();
    renderAt();
    expect(await screen.findByTestId("project-status-archived")).toHaveTextContent("archived");
  });

  it("falls back to task_id in a PR link's label when the ledger entry's title is empty", async () => {
    mockProject({
      engine_key: "DejvMatikar/alan",
      workspaces: [{ task_id: "untitled-ws", title: "", status: "completed", pr_number: 7 }],
    });
    mockDoc();
    renderAt();
    const prLink = await screen.findByTestId("project-pr-link-untitled-ws");
    expect(prLink).toHaveTextContent("PR #7 — untitled-ws");
  });

  it("does not render the project when the fetch resolves after unmount", () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    const gatePromise = new Promise<void>((resolve) => {
      gate.resolve = resolve;
    });
    server.use(
      http.get("/api/projects/alpha", async () => {
        await gatePromise;
        return HttpResponse.json(BASE_PROJECT);
      }),
    );
    mockDoc();
    const { unmount } = renderAt();
    unmount();
    gate.resolve?.();
  });

  it("does not surface an error when the fetch rejects after unmount", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.getProject;
    const gate: { reject: ((reason?: unknown) => void) | null } = { reject: null };
    apiModule.api.getProject = () =>
      new Promise((_resolve, reject) => {
        gate.reject = reject;
      });
    mockDoc();
    try {
      const { unmount } = renderAt();
      unmount();
      gate.reject?.(new Error("post-unmount load failure"));
    } finally {
      apiModule.api.getProject = original;
    }
  });

  it("reports non-Error load failures via String(err)", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.getProject;
    apiModule.api.getProject = () =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("string-load-error");
    mockDoc();
    try {
      renderAt();
      const alert = await screen.findByTestId("project-detail-error");
      expect(alert).toHaveTextContent("string-load-error");
    } finally {
      apiModule.api.getProject = original;
    }
  });

  it("renders nothing extra when routed without a project id", () => {
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <Routes>
          <Route path="/projects" element={<Project />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("project-detail-loading")).toBeInTheDocument();
  });

  it("clears the previous project's data while the next project's fetch is in flight", async () => {
    mockProject();
    server.use(
      http.get(
        "/api/projects/beta",
        () =>
          new Promise(() => undefined),
      ),
    );
    mockDoc();
    renderWithNav("/projects/alpha", "/projects/beta");
    expect(await screen.findByRole("heading", { level: 2, name: "Alpha" })).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("nav-to-other-project"));

    expect(screen.getByTestId("project-detail-loading")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Alpha" })).not.toBeInTheDocument();
  });
});
