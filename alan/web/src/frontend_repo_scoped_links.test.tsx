import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { HealthState } from "./api";
import { ArtifactsSection } from "./features/workspace/components/handoverSections";
import { HealthProvider } from "./api/HealthProvider";
import { server } from "./test/server";
import HealthView from "./features/health/pages/HealthView";

const BASE_HEALTH = {
  ok: true as const,
  queue_paused: false,
  paused_until: null,
};

function renderHealthView() {
  return render(
    <MemoryRouter>
      <HealthProvider>
        <HealthView />
      </HealthProvider>
    </MemoryRouter>,
  );
}

describe("frontend_repo_scoped_links — handoverSections.ArtifactsSection", () => {
  it("resolves the PR link and the SHA commit link against a non-default repo, never the default", () => {
    render(
      <ArtifactsSection
        artifacts={[
          { path: "abc1234567abcdef1234567abcdef1234567abcd", description: "Commit artifact" },
        ]}
        prNumber={7}
        taskId="t1"
        repo="acme-corp/sprocket"
      />,
    );
    expect(screen.getByTestId("pr-link")).toHaveAttribute(
      "href",
      "https://github.com/acme-corp/sprocket/pull/7",
    );
    const commitLink = screen.getByRole("link", { name: "Commit artifact" });
    const commitHref = commitLink.getAttribute("href") ?? "";
    expect(commitHref).toBe(
      "https://github.com/acme-corp/sprocket/commit/abc1234567abcdef1234567abcdef1234567abcd",
    );
    expect(commitHref).not.toContain("DejvMatikar/alan");
  });

  it("renders degraded, non-clickable PR and commit references — never a guessed default-repo link — when repo is unresolved", () => {
    render(
      <ArtifactsSection
        artifacts={[
          { path: "abc1234567abcdef1234567abcdef1234567abcd", description: "Commit artifact" },
        ]}
        prNumber={7}
        taskId="t1"
        repo={null}
      />,
    );
    const prLink = screen.getByTestId("pr-link");
    expect(prLink.tagName).toBe("SPAN");
    expect(prLink).toHaveTextContent("PR #7");
    expect(prLink).toHaveTextContent("repository unresolved");
    expect(screen.queryByRole("link", { name: "Commit artifact" })).not.toBeInTheDocument();
    expect(screen.getByText(/Commit artifact/)).toHaveTextContent("repository unresolved");
  });

  it("leaves local-path and external-URL artifact links unaffected by an unresolved repo", () => {
    render(
      <ArtifactsSection
        artifacts={[
          { path: "invocations/inv_001/report.md", description: "Local report" },
          { path: "https://example.com/spec", description: "External spec" },
        ]}
        prNumber={null}
        taskId="t1"
        repo={null}
      />,
    );
    expect(screen.getByRole("link", { name: "Local report" })).toHaveAttribute(
      "href",
      "/api/workspaces/t1/artifacts?path=invocations%2Finv_001%2Freport.md",
    );
    expect(screen.getByRole("link", { name: "External spec" })).toHaveAttribute(
      "href",
      "https://example.com/spec",
    );
  });
});

describe("frontend_repo_scoped_links — HealthView finding links", () => {
  it("resolves a finding's PR link against its own non-default repo, never the default", async () => {
    const state: HealthState = {
      verdict: "degraded",
      blocked_count: 1,
      in_flight_count: 0,
      findings: [
        {
          finding_id: "task-sprocket",
          kind: "build_task",
          severity: "critical",
          description: "Sprocket engine finding",
          workspace_id: "task-sprocket",
          pr_number: 9,
          blocker_flags: [],
          action: "dispatch_remediation",
          engine_key: "acme-corp/sprocket",
          in_flight: false,
        },
      ],
      computed_at: new Date().toISOString(),
    };
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: state })),
    );
    renderHealthView();
    await waitFor(() => {
      expect(screen.getByTestId("finding-pr-link-task-sprocket")).toHaveAttribute(
        "href",
        "https://github.com/acme-corp/sprocket/pull/9",
      );
    });
  });

  it("renders a degraded, non-clickable PR reference — never a guessed default-repo link — when a finding carries no repo", async () => {
    const state: HealthState = {
      verdict: "degraded",
      blocked_count: 1,
      in_flight_count: 0,
      findings: [
        {
          finding_id: "task-default",
          kind: "build_task",
          severity: "critical",
          description: "No-repo finding",
          workspace_id: "task-default",
          pr_number: 42,
          blocker_flags: [],
          action: "dispatch_remediation",
          in_flight: false,
        },
      ],
      computed_at: new Date().toISOString(),
    };
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: state })),
    );
    renderHealthView();
    await waitFor(() => {
      const el = screen.getByTestId("finding-pr-link-task-default");
      expect(el.tagName).toBe("SPAN");
      expect(el).toHaveTextContent("PR #42");
      expect(el).toHaveTextContent("repository unresolved");
    });
  });
});
