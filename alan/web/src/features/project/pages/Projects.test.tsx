import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import Projects from "./Projects";

function renderAt(initial = "/projects") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/projects" element={<Projects />} />
        <Route
          path="/projects/:projectId"
          element={<div data-testid="detail-page">detail</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

const SAMPLE_PROJECTS = [
  {
    id: "alpha",
    title: "Alpha",
    status: "active",
    doc_ref: null,
    created_at: "2026-05-10T08:00:00Z",
    updated_at: "2026-05-10T08:00:00Z",
    workspace_count: 3,
    total_spend_usd: 12.5,
  },
  {
    id: "beta",
    title: "Beta",
    status: "archived",
    doc_ref: "docs/beta.md",
    created_at: "2026-05-09T08:00:00Z",
    updated_at: "2026-05-09T08:00:00Z",
    workspace_count: 0,
    total_spend_usd: 0,
  },
];

describe("Projects", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("renders a single top-level Projects heading", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderAt();
    expect(await screen.findByRole("heading", { level: 1, name: "Projects" })).toBeInTheDocument();
  });

  it("shows a loading state before the fetch resolves", () => {
    server.use(
      http.get("/api/projects", async () => {
        await new Promise(() => undefined);
        return HttpResponse.json([]);
      }),
    );
    renderAt();
    expect(screen.getByTestId("projects-loading")).toBeInTheDocument();
  });

  it("shows an empty state when there are no projects", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderAt();
    expect(await screen.findByTestId("projects-empty")).toHaveTextContent(
      "No active projects yet.",
    );
  });

  it("renders the active project's title and status, hiding the archived one by default", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json(SAMPLE_PROJECTS)));
    renderAt();
    expect(await screen.findByRole("link", { name: "Alpha" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Beta" })).not.toBeInTheDocument();
    expect(screen.getByTestId("project-status-active")).toHaveTextContent("active");
    expect(screen.getByTestId("project-status-active").className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-status-active-tint/15 text-status-active-fg",
    );
  });

  it("reveals the archived project's status once the Archived tab is selected", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json(SAMPLE_PROJECTS)));
    renderAt();
    await screen.findByRole("link", { name: "Alpha" });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-tab-archived"));
    expect(await screen.findByRole("link", { name: "Beta" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Alpha" })).not.toBeInTheDocument();
    expect(screen.getByTestId("project-status-archived")).toHaveTextContent("archived");
    expect(screen.getByTestId("project-status-archived").className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-status-neutral-tint/15 text-status-neutral-fg",
    );
  });

  it("renders each project's workspace count and total spend from the list API", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json(SAMPLE_PROJECTS)));
    renderAt();
    const alphaStats = await screen.findByTestId("project-stats-alpha");
    expect(alphaStats).toHaveTextContent("3 workspaces");
    expect(alphaStats).toHaveTextContent("$12.50");

    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-tab-archived"));
    const betaStats = await screen.findByTestId("project-stats-beta");
    expect(betaStats).toHaveTextContent("0 workspaces");
    expect(betaStats).toHaveTextContent("$0.00");
  });

  it("uses singular 'workspace' when the count is exactly one", async () => {
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([
          {
            id: "solo",
            title: "Solo",
            status: "active",
            doc_ref: null,
            created_at: "2026-05-10T08:00:00Z",
            updated_at: "2026-05-10T08:00:00Z",
            workspace_count: 1,
            total_spend_usd: 4,
          },
        ]),
      ),
    );
    renderAt();
    const stats = await screen.findByTestId("project-stats-solo");
    expect(stats.textContent).toBe("1 workspace · $4.00");
  });

  it("renders a shipped badge for completed projects", async () => {
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([
          {
            id: "gamma",
            title: "Gamma",
            status: "completed",
            doc_ref: null,
            created_at: "2026-05-08T08:00:00Z",
            updated_at: "2026-05-08T08:00:00Z",
            workspace_count: 1,
            total_spend_usd: 0,
          },
        ]),
      ),
    );
    renderAt();
    const badge = await screen.findByTestId("project-status-completed");
    expect(badge).toHaveTextContent("shipped");
    expect(badge.className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-status-success-tint/15 text-status-success-fg",
    );
  });

  it("renders a provisioning badge for provisioning projects", async () => {
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([
          {
            id: "delta",
            title: "Delta",
            status: "provisioning",
            doc_ref: null,
            created_at: "2026-05-08T08:00:00Z",
            updated_at: "2026-05-08T08:00:00Z",
            workspace_count: 0,
            total_spend_usd: 0,
          },
        ]),
      ),
    );
    renderAt();
    const badge = await screen.findByTestId("project-status-provisioning");
    expect(badge).toHaveTextContent("provisioning");
    expect(badge.className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-status-attention-tint/15 text-status-attention-fg",
    );
  });

  it("requests archived projects alongside active ones", async () => {
    let requestedInclude: string | null = null;
    server.use(
      http.get("/api/projects", ({ request }) => {
        requestedInclude = new URL(request.url).searchParams.get("include");
        return HttpResponse.json([]);
      }),
    );
    renderAt();
    await screen.findByTestId("projects-empty");
    expect(requestedInclude).toBe("archived");
  });

  it("clicking a project title navigates to its detail page", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json(SAMPLE_PROJECTS)));
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "Alpha" }));
    expect(await screen.findByTestId("detail-page")).toBeInTheDocument();
  });

  it("renders an error alert when listProjects fails", async () => {
    server.use(
      http.get("/api/projects", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderAt();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("500");
    expect(alert).toHaveTextContent("boom");
  });

  it("reports non-Error load failures via String(err)", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.listProjects;
    apiModule.api.listProjects = () =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("string-load-error");
    try {
      renderAt();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("string-load-error");
    } finally {
      apiModule.api.listProjects = original;
    }
  });

  it("New project button opens the modal", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    expect(screen.getByTestId("new-project-dialog")).toBeInTheDocument();
  });

  it("Create project is disabled until a title is entered", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    expect(screen.getByTestId("new-project-submit")).toBeDisabled();
    await user.type(screen.getByTestId("new-project-title"), "New Thing");
    expect(screen.getByTestId("new-project-submit")).not.toBeDisabled();
  });

  it("submitting the New project form creates a project and navigates to its detail page", async () => {
    server.use(
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", async ({ request }) => {
        const body = (await request.json()) as { title: string; doc_ref: string | null };
        return HttpResponse.json(
          {
            id: "new-thing",
            title: body.title,
            status: "active",
            doc_ref: body.doc_ref,
            created_at: "2026-06-01T00:00:00Z",
            updated_at: "2026-06-01T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    await user.type(screen.getByTestId("new-project-title"), "New Thing");
    await user.type(screen.getByTestId("new-project-doc-ref"), "docs/x.md");
    await user.click(screen.getByTestId("new-project-submit"));
    expect(await screen.findByTestId("detail-page")).toBeInTheDocument();
  });

  it("blank doc_ref is submitted as null", async () => {
    let capturedDocRef: string | null | undefined;
    server.use(
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", async ({ request }) => {
        const body = (await request.json()) as { title: string; doc_ref: string | null };
        capturedDocRef = body.doc_ref;
        return HttpResponse.json(
          {
            id: "no-doc",
            title: body.title,
            status: "active",
            doc_ref: null,
            created_at: "2026-06-01T00:00:00Z",
            updated_at: "2026-06-01T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    await user.type(screen.getByTestId("new-project-title"), "No Doc");
    await user.click(screen.getByTestId("new-project-submit"));
    await screen.findByTestId("detail-page");
    expect(capturedDocRef).toBeNull();
  });

  it("shows an inline alert when project creation fails and keeps the modal open", async () => {
    server.use(
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", () => HttpResponse.json({ detail: "create-boom" }, { status: 500 })),
    );
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    await user.type(screen.getByTestId("new-project-title"), "Doomed");
    await user.click(screen.getByTestId("new-project-submit"));
    const alert = await screen.findByTestId("new-project-error");
    expect(alert).toHaveTextContent("create-boom");
    expect(alert.className).toContain("text-btn-destroy");
    expect(screen.getByTestId("new-project-dialog")).toBeInTheDocument();
  });

  it("Escape closes the New project modal", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    const form = screen.getByTestId("new-project-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    expect(screen.queryByTestId("new-project-dialog")).not.toBeInTheDocument();
  });

  it("Cancel button closes the New project modal", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("new-project-dialog")).not.toBeInTheDocument();
  });

  it("Ctrl+Enter submits the New project form", async () => {
    server.use(
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", () =>
        HttpResponse.json(
          {
            id: "ctrl-enter",
            title: "Ctrl Enter",
            status: "active",
            doc_ref: null,
            created_at: "2026-06-01T00:00:00Z",
            updated_at: "2026-06-01T00:00:00Z",
          },
          { status: 201 },
        ),
      ),
    );
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    await user.type(screen.getByTestId("new-project-title"), "Ctrl Enter");
    const form = screen.getByTestId("new-project-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(await screen.findByTestId("detail-page")).toBeInTheDocument();
  });

  it("Ctrl+Enter with a blank title does not submit", async () => {
    let posted = false;
    server.use(
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", () => {
        posted = true;
        return HttpResponse.json(
          {
            id: "should-not-happen",
            title: "",
            status: "active",
            doc_ref: null,
            created_at: "2026-06-01T00:00:00Z",
            updated_at: "2026-06-01T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    const form = screen.getByTestId("new-project-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("new-project-dialog")).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it("submitting the form directly (not via the buttons) does not navigate away", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    const form = screen.getByTestId("new-project-dialog").querySelector("form")!;
    fireEvent.submit(form);
    expect(screen.getByTestId("new-project-dialog")).toBeInTheDocument();
  });

  it("blocks Escape and Ctrl+Enter while a create request is in flight", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post(
        "/api/projects",
        () =>
          new Promise((resolve) => {
            gate.resolve = () =>
              resolve(
                HttpResponse.json(
                  {
                    id: "slow",
                    title: "Slow",
                    status: "active",
                    doc_ref: null,
                    created_at: "2026-06-01T00:00:00Z",
                    updated_at: "2026-06-01T00:00:00Z",
                  },
                  { status: 201 },
                ),
              );
          }),
      ),
    );
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    await user.type(screen.getByTestId("new-project-title"), "Slow");
    await user.click(screen.getByTestId("new-project-submit"));
    expect(screen.getByTestId("new-project-submit")).toHaveTextContent("Creating…");

    const form = screen.getByTestId("new-project-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("new-project-dialog")).toBeInTheDocument();

    gate.resolve?.();
    await screen.findByTestId("detail-page");
  });

  it("the dialog's native cancel event closes the modal when idle", async () => {
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    const dialog = screen.getByTestId("new-project-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(screen.queryByTestId("new-project-dialog")).not.toBeInTheDocument();
  });

  it("blocks the dialog's native cancel event while a create request is in flight", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post(
        "/api/projects",
        () =>
          new Promise((resolve) => {
            gate.resolve = () =>
              resolve(
                HttpResponse.json(
                  {
                    id: "slow-cancel",
                    title: "Slow Cancel",
                    status: "active",
                    doc_ref: null,
                    created_at: "2026-06-01T00:00:00Z",
                    updated_at: "2026-06-01T00:00:00Z",
                  },
                  { status: 201 },
                ),
              );
          }),
      ),
    );
    renderAt();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("new-project-button"));
    await user.type(screen.getByTestId("new-project-title"), "Slow Cancel");
    await user.click(screen.getByTestId("new-project-submit"));
    expect(screen.getByTestId("new-project-submit")).toHaveTextContent("Creating…");

    const dialog = screen.getByTestId("new-project-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(screen.getByTestId("new-project-dialog")).toBeInTheDocument();

    gate.resolve?.();
    await screen.findByTestId("detail-page");
  });

  describe("project list tabs", () => {
    it("Active tab is selected by default", async () => {
      server.use(http.get("/api/projects", () => HttpResponse.json([])));
      renderAt();
      await screen.findByTestId("projects-empty");
      expect(screen.getByRole("tab", { name: "Active" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(screen.getByRole("tab", { name: "Archived" })).toHaveAttribute(
        "aria-selected",
        "false",
      );
    });

    it("restores the selected tab from sessionStorage on mount", async () => {
      server.use(http.get("/api/projects", () => HttpResponse.json([])));
      sessionStorage.setItem("project-list-tab", "archived");
      renderAt();
      await screen.findByTestId("projects-empty");
      expect(screen.getByRole("tab", { name: "Archived" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("persists tab selection to sessionStorage on click", async () => {
      server.use(http.get("/api/projects", () => HttpResponse.json([])));
      renderAt();
      const user = userEvent.setup();
      await user.click(await screen.findByRole("tab", { name: "Archived" }));
      expect(sessionStorage.getItem("project-list-tab")).toBe("archived");
    });

    it("shows the Archived-tab empty message once it has no archived projects", async () => {
      server.use(http.get("/api/projects", () => HttpResponse.json([])));
      renderAt();
      const user = userEvent.setup();
      await user.click(await screen.findByTestId("project-tab-archived"));
      expect(await screen.findByTestId("projects-empty")).toHaveTextContent(
        "No archived projects yet.",
      );
    });

    it("keeps completed projects visible in the Active tab", async () => {
      server.use(
        http.get("/api/projects", () =>
          HttpResponse.json([
            {
              id: "shipped-one",
              title: "Shipped One",
              status: "completed",
              doc_ref: null,
              created_at: "2026-05-08T08:00:00Z",
              updated_at: "2026-05-08T08:00:00Z",
              workspace_count: 1,
              total_spend_usd: 0,
            },
          ]),
        ),
      );
      renderAt();
      expect(await screen.findByRole("link", { name: "Shipped One" })).toBeInTheDocument();
    });

    it("ArrowRight on the tablist moves from Active to Archived and persists it", async () => {
      server.use(http.get("/api/projects", () => HttpResponse.json([])));
      renderAt();
      await screen.findByTestId("projects-empty");
      fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
      expect(screen.getByRole("tab", { name: "Archived" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(sessionStorage.getItem("project-list-tab")).toBe("archived");
    });

    it("ArrowRight clamps at the last tab instead of wrapping", async () => {
      server.use(http.get("/api/projects", () => HttpResponse.json([])));
      sessionStorage.setItem("project-list-tab", "archived");
      renderAt();
      await screen.findByTestId("projects-empty");
      fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
      expect(screen.getByRole("tab", { name: "Archived" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("ArrowLeft on the tablist moves from Archived back to Active", async () => {
      server.use(http.get("/api/projects", () => HttpResponse.json([])));
      sessionStorage.setItem("project-list-tab", "archived");
      renderAt();
      await screen.findByTestId("projects-empty");
      fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
      expect(screen.getByRole("tab", { name: "Active" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("a non-arrow key on the tablist does not change tabs", async () => {
      server.use(http.get("/api/projects", () => HttpResponse.json([])));
      renderAt();
      await screen.findByTestId("projects-empty");
      fireEvent.keyDown(screen.getByRole("tablist"), { key: "Enter" });
      expect(screen.getByRole("tab", { name: "Active" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
  });
});
