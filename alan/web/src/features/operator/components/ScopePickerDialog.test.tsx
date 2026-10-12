import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http, type JsonBodyType } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { WorkspaceSummary } from "../../../api";
import type { OperatorSession } from "../../../api/operator";
import { deferredFetch } from "../../../test/docReadHarness";
import {
  engineListingHandler,
  gatedEngineListingHandler,
  makeEngineSummaryFor,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import ScopePickerDialog, { type ScopePickerDialogProps } from "./ScopePickerDialog";

const RUNNING: OperatorSession = {
  scope: "repo",
  target: "acme/widget",
  started_at: "2026-09-29T10:00:00Z",
  update_warning: null,
  exited: false,
};

const STARTED = { ...RUNNING, scope: "fleet", target: null };

function serveStart(status = 200, body: JsonBodyType = STARTED) {
  const seen: unknown[] = [];
  server.use(
    http.post("/api/operator/session", async ({ request }) => {
      seen.push(await request.json());
      return HttpResponse.json(body, { status });
    }),
  );
  return seen;
}

function serveRepos(...keys: string[]) {
  server.use(
    engineListingHandler({ engines: keys.map((key) => makeEngineSummaryFor(key)) }),
  );
}

function stuck(taskId: string, overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return makeWorkspaceSummary({
    task_id: taskId,
    title: `Title of ${taskId}`,
    status: "escalated",
    operator_link: true,
    engine_key: "acme/widget",
    ...overrides,
  });
}

function serveWorkspaces(...rows: WorkspaceSummary[]) {
  server.use(http.get("/api/workspaces", () => HttpResponse.json(rows)));
}

function renderDialog(overrides: Partial<ScopePickerDialogProps> = {}) {
  const props: ScopePickerDialogProps = {
    preset: { scope: "fleet", target: "" },
    replacing: null,
    onStarted: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
  render(<ScopePickerDialog {...props} />);
  return props;
}

async function awaitOption(value: string) {
  await waitFor(() => {
    expect(within(screen.getByTestId("operator-target")).getByRole("option", { name: value })).toBeEnabled();
  });
}

describe("ScopePickerDialog — choosing a scope", () => {
  it("offers global, one repository and one workspace, with global chosen by default", () => {
    renderDialog();
    expect(screen.getByRole("dialog", { name: "Start an operator session" })).toBeInTheDocument();
    expect(screen.getByLabelText(/^Global/)).toBeChecked();
    expect(screen.getByLabelText(/One repository/)).not.toBeChecked();
    expect(screen.getByLabelText(/One workspace/)).not.toBeChecked();
    expect(screen.getByTestId("operator-scope-fleet")).toHaveAttribute("value", "fleet");
    expect(screen.queryByTestId("operator-target")).not.toBeInTheDocument();
  });

  it("describes what each scope actually does", () => {
    renderDialog();
    expect(screen.getByLabelText(/^Global/)).toHaveAccessibleName(
      /every connected repository.*workspaces, logs and state folders/,
    );
    expect(screen.getByLabelText(/One repository/)).toHaveAccessibleName(
      /that repository's operator checkout/,
    );
    expect(screen.getByLabelText(/One workspace/)).toHaveAccessibleName(
      /analysing that workspace straight away/,
    );
  });

  it("no longer claims the scope never changes permissions", () => {
    renderDialog();
    expect(screen.queryByText(/never changes permissions/)).not.toBeInTheDocument();
  });

  it("shows a repository dropdown instead of a free-text field once a repository scope is chosen", async () => {
    renderDialog();
    await userEvent.setup().click(screen.getByTestId("operator-scope-repo"));
    const field = screen.getByLabelText("Repository");
    expect(field.tagName).toBe("SELECT");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await awaitOption("DejvMatikar/alan");
  });

  it("shows a workspace dropdown once a workspace scope is chosen", async () => {
    serveWorkspaces(stuck("fix-it"));
    renderDialog();
    await userEvent.setup().click(screen.getByTestId("operator-scope-workspace"));
    expect(screen.getByLabelText("Workspace").tagName).toBe("SELECT");
    await awaitOption("Title of fix-it · escalated · acme/widget");
  });

  it("opens on the preset scope with the preset workspace selected", async () => {
    serveWorkspaces(stuck("fix-it"), stuck("other"));
    renderDialog({ preset: { scope: "workspace", target: "fix-it" } });
    expect(screen.getByLabelText(/One workspace/)).toBeChecked();
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("fix-it");
    });
  });

  it("keeps the picked repository when the scope is switched away and back", async () => {
    const user = userEvent.setup();
    serveRepos("acme/widget", "acme/gadget");
    renderDialog({ preset: { scope: "repo", target: "" } });
    await awaitOption("acme/widget");
    await user.selectOptions(screen.getByTestId("operator-target"), "acme/gadget");
    await user.click(screen.getByTestId("operator-scope-fleet"));
    await user.click(screen.getByTestId("operator-scope-repo"));
    expect(screen.getByTestId("operator-target")).toHaveValue("acme/gadget");
  });

  it("does not carry a workspace id over into the repository list", async () => {
    const user = userEvent.setup();
    serveRepos("acme/widget", "acme/gadget");
    serveWorkspaces(stuck("fix-it"));
    renderDialog({ preset: { scope: "workspace", target: "fix-it" } });
    await user.click(screen.getByTestId("operator-scope-repo"));
    await awaitOption("acme/widget");
    expect(screen.getByTestId("operator-target")).toHaveValue("");
  });
});

describe("ScopePickerDialog — the repository list", () => {
  it("preselects the only connected repository and lets it start", async () => {
    serveRepos("acme/widget");
    renderDialog({ preset: { scope: "repo", target: "" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("acme/widget");
    });
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
  });

  it("keeps Start disabled while several repositories are listed and none is chosen", async () => {
    const user = userEvent.setup();
    serveRepos("acme/widget", "acme/gadget");
    renderDialog({ preset: { scope: "repo", target: "" } });
    await awaitOption("acme/widget");
    expect(screen.getByTestId("operator-target")).toHaveValue("");
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
    await user.selectOptions(screen.getByTestId("operator-target"), "acme/widget");
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
  });

  it("preselects a repository named by the link when it is connected", async () => {
    serveRepos("acme/widget", "acme/gadget");
    renderDialog({ preset: { scope: "repo", target: "acme/gadget" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("acme/gadget");
    });
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
  });

  it("disables the dropdown and Start while the list loads, then enables them", async () => {
    const gated = gatedEngineListingHandler({ engines: [makeEngineSummaryFor("acme/widget")] });
    server.use(gated.handler);
    renderDialog({ preset: { scope: "repo", target: "" } });
    const select = screen.getByTestId("operator-target");
    expect(select).toBeDisabled();
    expect(within(select).getByRole("option", { name: "Loading repositories…" })).toBeInTheDocument();
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
    gated.release();
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toBeEnabled();
    });
  });

  it("shows an inline error and keeps Start disabled when the list cannot be loaded", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.error()));
    renderDialog({ preset: { scope: "repo", target: "" } });
    expect(await screen.findByTestId("operator-target-error")).toHaveTextContent(
      "The repository list could not be loaded.",
    );
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
  });

  it("says so and keeps Start disabled when no repository is connected", async () => {
    serveRepos();
    renderDialog({ preset: { scope: "repo", target: "" } });
    expect(await screen.findByTestId("operator-target-empty")).toHaveTextContent(
      "No repository is connected yet.",
    );
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
  });
});

describe("ScopePickerDialog — the workspace list", () => {
  it("lists stuck workspaces and keeps Start disabled until one is chosen", async () => {
    const user = userEvent.setup();
    serveWorkspaces(
      stuck("fix-it"),
      makeWorkspaceSummary({ task_id: "healthy", title: "Healthy", operator_link: false }),
      stuck("broken", { status: "failed" }),
    );
    renderDialog({ preset: { scope: "workspace", target: "" } });
    await awaitOption("Title of fix-it · escalated · acme/widget");
    const options = within(screen.getByTestId("operator-target")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Choose a workspace",
      "Title of fix-it · escalated · acme/widget",
      "Title of broken · failed · acme/widget",
    ]);
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
    await user.selectOptions(screen.getByTestId("operator-target"), "broken");
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
  });

  it("keeps the workspace named by the link selected after it stopped being stuck", async () => {
    serveWorkspaces(
      makeWorkspaceSummary({
        task_id: "fix-it",
        title: "Fixed itself",
        status: "completed",
        operator_link: false,
        engine_key: "acme/widget",
      }),
    );
    renderDialog({ preset: { scope: "workspace", target: "fix-it" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("fix-it");
    });
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
  });

  it("disables the dropdown and Start while the list loads", async () => {
    const gate = deferredFetch();
    server.use(
      http.get("/api/workspaces", async () => {
        await gate.settled;
        return HttpResponse.json([stuck("fix-it")]);
      }),
    );
    renderDialog({ preset: { scope: "workspace", target: "fix-it" } });
    const select = screen.getByTestId("operator-target");
    expect(select).toBeDisabled();
    expect(within(select).getByRole("option", { name: "Loading workspaces…" })).toBeInTheDocument();
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
    gate.release();
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toBeEnabled();
    });
  });

  it("shows an inline error and keeps Start disabled when the list cannot be loaded", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderDialog({ preset: { scope: "workspace", target: "fix-it" } });
    expect(await screen.findByTestId("operator-target-error")).toHaveTextContent(
      "The workspace list could not be loaded.",
    );
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
  });

  it("says nothing needs the operator and keeps Start disabled when no workspace is stuck", async () => {
    serveWorkspaces(makeWorkspaceSummary({ task_id: "healthy", operator_link: false }));
    renderDialog({ preset: { scope: "workspace", target: "" } });
    expect(await screen.findByTestId("operator-target-empty")).toHaveTextContent(
      "Nothing needs the operator right now.",
    );
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
  });
});

describe("ScopePickerDialog — starting a session", () => {
  it("starts a global session with no target and no replace, then reports it started", async () => {
    const seen = serveStart();
    const props = renderDialog();
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
    await userEvent.setup().click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(seen).toEqual([{ scope: "fleet", target: null, replace: false }]);
  });

  it("starts a repository session with the picked repository", async () => {
    const user = userEvent.setup();
    const seen = serveStart();
    serveRepos("acme/widget", "acme/gadget");
    const props = renderDialog({ preset: { scope: "repo", target: "" } });
    await awaitOption("acme/gadget");
    await user.selectOptions(screen.getByTestId("operator-target"), "acme/gadget");
    await user.click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(seen).toEqual([{ scope: "repo", target: "acme/gadget", replace: false }]);
  });

  it("starts a repository session on the only repository without a pick", async () => {
    const seen = serveStart();
    serveRepos("acme/widget");
    const props = renderDialog({ preset: { scope: "repo", target: "" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
    });
    await userEvent.setup().click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(seen).toEqual([{ scope: "repo", target: "acme/widget", replace: false }]);
  });

  it("starts a workspace session with the workspace id", async () => {
    const seen = serveStart();
    serveWorkspaces(stuck("fix-it"));
    const props = renderDialog({ preset: { scope: "workspace", target: "fix-it" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
    });
    await userEvent.setup().click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(seen).toEqual([{ scope: "workspace", target: "fix-it", replace: false }]);
  });

  it("drops a repository picked for another scope when the global scope is started", async () => {
    const user = userEvent.setup();
    const seen = serveStart();
    serveRepos("acme/widget");
    const props = renderDialog({ preset: { scope: "repo", target: "acme/widget" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("acme/widget");
    });
    await user.click(screen.getByTestId("operator-scope-fleet"));
    await user.click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(seen).toEqual([{ scope: "fleet", target: null, replace: false }]);
  });

  it("starts with Ctrl+Enter from the dropdown once a valid choice is made", async () => {
    const seen = serveStart();
    serveRepos("acme/widget");
    const props = renderDialog({ preset: { scope: "repo", target: "" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("acme/widget");
    });
    screen.getByTestId("operator-target").focus();
    await userEvent.setup().keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(seen).toHaveLength(1);
  });

  it("does not start by keyboard while no repository is chosen", async () => {
    const seen = serveStart();
    serveRepos("acme/widget", "acme/gadget");
    const props = renderDialog({ preset: { scope: "repo", target: "" } });
    await awaitOption("acme/widget");
    screen.getByTestId("operator-target").focus();
    await userEvent.setup().keyboard("{Control>}{Enter}{/Control}");
    expect(seen).toEqual([]);
    expect(props.onStarted).not.toHaveBeenCalled();
  });

  it("puts the dropdown between the scope choice and the dialog buttons in the tab order", async () => {
    const user = userEvent.setup();
    serveRepos("acme/widget", "acme/gadget");
    renderDialog({ preset: { scope: "repo", target: "" } });
    await awaitOption("acme/widget");
    screen.getByTestId("operator-scope-repo").focus();
    await user.tab();
    expect(screen.getByTestId("operator-target")).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("locks the dialog and shows no second request while the start is in flight", async () => {
    const gate = deferredFetch();
    let calls = 0;
    server.use(
      http.post("/api/operator/session", async () => {
        calls += 1;
        await gate.settled;
        return HttpResponse.json(STARTED);
      }),
    );
    const props = renderDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.keyboard("{Control>}{Enter}{/Control}");
    gate.release();
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(calls).toBe(1);
  });

  it("shows the server's reason and stays open when the start is refused", async () => {
    serveStart(409, { detail: "an operator session is already running; ask to replace it" });
    const props = renderDialog();
    await userEvent.setup().click(screen.getByTestId("operator-start-submit"));
    expect(await screen.findByTestId("operator-start-error")).toHaveTextContent(
      "an operator session is already running; ask to replace it",
    );
    expect(props.onStarted).not.toHaveBeenCalled();
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
  });

  it("shows the server's reason when a repository that is not connected is refused", async () => {
    const user = userEvent.setup();
    serveStart(422, { detail: "acme/widget is not a connected repository" });
    serveRepos("acme/widget");
    renderDialog({ preset: { scope: "repo", target: "" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
    });
    await user.click(screen.getByTestId("operator-start-submit"));
    expect(await screen.findByTestId("operator-start-error")).toHaveTextContent(
      "acme/widget is not a connected repository",
    );
  });

  it("shows the exact unavailable sentence for a 503", async () => {
    serveStart(503, { detail: { reason: "tmux_missing", detail: "tmux is not installed." } });
    renderDialog();
    await userEvent.setup().click(screen.getByTestId("operator-start-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("tmux is not installed.");
  });

  it("shows the failure message when the service cannot be reached", async () => {
    server.use(http.post("/api/operator/session", () => HttpResponse.error()));
    renderDialog();
    await userEvent.setup().click(screen.getByTestId("operator-start-submit"));
    expect(await screen.findByTestId("operator-start-error")).not.toBeEmptyDOMElement();
  });

  it("clears an earlier error when the start is tried again", async () => {
    let calls = 0;
    server.use(
      http.post("/api/operator/session", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "first failure" }, { status: 500 })
          : HttpResponse.json(STARTED);
      }),
    );
    const props = renderDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-start-submit"));
    expect(await screen.findByTestId("operator-start-error")).toHaveTextContent("first failure");
    await user.click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByTestId("operator-start-error")).not.toBeInTheDocument();
  });
});

describe("ScopePickerDialog — replacing a running session", () => {
  it("says what is running and asks for an explicit confirmation before replacing it", () => {
    renderDialog({ replacing: RUNNING });
    expect(screen.getByTestId("operator-replace-notice")).toHaveTextContent(
      "A session is already running (Repository session · acme/widget). Starting a new one ends it first.",
    );
    expect(screen.getByTestId("operator-replace-confirm")).not.toBeChecked();
    expect(screen.getByTestId("operator-start-submit")).toHaveTextContent("Replace session");
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
  });

  it("stays blocked on a keyboard submit until the replace is confirmed", async () => {
    const seen = serveStart();
    renderDialog({ replacing: RUNNING });
    screen.getByRole("radio", { name: /^Global/ }).focus();
    await userEvent.setup().keyboard("{Control>}{Enter}{/Control}");
    expect(seen).toEqual([]);
  });

  it("sends replace: true once the replace is confirmed", async () => {
    const user = userEvent.setup();
    const seen = serveStart();
    const props = renderDialog({ replacing: RUNNING });
    await user.click(screen.getByTestId("operator-replace-confirm"));
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
    await user.click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(props.onStarted).toHaveBeenCalledTimes(1);
    });
    expect(seen).toEqual([{ scope: "fleet", target: null, replace: true }]);
  });

  it("keeps a repository start disabled until both a repository and the replace are chosen", async () => {
    const user = userEvent.setup();
    serveRepos("acme/widget");
    renderDialog({ replacing: RUNNING, preset: { scope: "repo", target: "" } });
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("acme/widget");
    });
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
    await user.click(screen.getByTestId("operator-replace-confirm"));
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
  });

  it("blocks again when the confirmation is withdrawn", async () => {
    const user = userEvent.setup();
    renderDialog({ replacing: RUNNING });
    await user.click(screen.getByTestId("operator-replace-confirm"));
    await user.click(screen.getByTestId("operator-replace-confirm"));
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
  });

  it("shows no replace confirmation when nothing is running", () => {
    renderDialog();
    expect(screen.queryByTestId("operator-replace-confirm")).not.toBeInTheDocument();
    expect(screen.getByTestId("operator-start-submit")).toHaveTextContent("Start session");
  });
});

describe("ScopePickerDialog — leaving without starting", () => {
  it("cancels from the Cancel button", async () => {
    const props = renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it("cancels on Escape", async () => {
    const props = renderDialog();
    screen.getByTestId("operator-scope-fleet").focus();
    await userEvent.setup().keyboard("{Escape}");
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });
});
