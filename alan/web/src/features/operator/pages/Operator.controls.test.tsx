import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http, type JsonBodyType } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkspaceSummary } from "../../../api";
import { deferredFetch } from "../../../test/docReadHarness";
import { engineListingHandler, makeEngineSummaryFor } from "../../../test/engineFixtures";
import { FakeTerminal } from "../../../test/fakeXterm";
import { FakeWebSocket } from "../../../test/fakeWebSocket";
import { server } from "../../../test/server";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import Operator from "./Operator";

vi.mock("@xterm/xterm", async () => {
  const { FakeTerminal: Terminal } = await import("../../../test/fakeXterm");
  return { Terminal };
});

vi.mock("@xterm/addon-fit", async () => {
  const { FakeFitAddon: FitAddon } = await import("../../../test/fakeXterm");
  return { FitAddon };
});

const FLEET = {
  session: {
    scope: "fleet",
    target: null,
    started_at: "2026-09-29T10:00:00Z",
    update_warning: null,
    exited: false,
  },
  unavailable: null,
};
const REPLACEMENT = {
  session: { ...FLEET.session, scope: "workspace", target: "fix-it", started_at: "2026-09-29T11:00:00Z" },
  unavailable: null,
};
const NONE = { session: null, unavailable: null };
const BLOCKED = {
  session: null,
  unavailable: { reason: "tmux_missing", detail: "tmux is not installed." },
};

interface Tally {
  status: number;
  tickets: number;
  starts: unknown[];
  ends: number;
}

function serve(statusSteps: JsonBodyType[]): Tally {
  const tally: Tally = { status: 0, tickets: 0, starts: [], ends: 0 };
  server.use(
    http.get("/api/operator/status", () => {
      const step = statusSteps[Math.min(tally.status, statusSteps.length - 1)];
      tally.status += 1;
      return HttpResponse.json(step);
    }),
    http.post("/api/operator/attach-ticket", () => {
      tally.tickets += 1;
      return HttpResponse.json({ ticket: `tk-${String(tally.tickets)}`, expires_in_s: 30 });
    }),
    http.post("/api/operator/session", async ({ request }) => {
      tally.starts.push(await request.json());
      return HttpResponse.json(REPLACEMENT.session);
    }),
    http.delete("/api/operator/session", () => {
      tally.ends += 1;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return tally;
}

function serveRepos(...keys: string[]) {
  server.use(engineListingHandler({ engines: keys.map((key) => makeEngineSummaryFor(key)) }));
}

function serveStuckWorkspace(taskId: string) {
  const row: WorkspaceSummary = makeWorkspaceSummary({
    task_id: taskId,
    title: `Title of ${taskId}`,
    status: "escalated",
    operator_link: true,
    engine_key: "acme/widget",
  });
  server.use(http.get("/api/workspaces", () => HttpResponse.json([row])));
}

async function enabledTarget(): Promise<HTMLElement> {
  const target = screen.getByTestId("operator-target");
  await waitFor(() => {
    expect(target).toBeEnabled();
  });
  return target;
}

function renderOperator(url = "/operator") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/operator" element={<Operator />} />
        <Route path="/" element={<div data-testid="home-page">home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function attach(index = 0): Promise<FakeWebSocket> {
  await waitFor(() => {
    expect(FakeWebSocket.instances.length).toBeGreaterThan(index);
  });
  const socket = FakeWebSocket.instances[index]!;
  act(() => {
    socket.serverOpen();
  });
  await screen.findByTestId("operator-notice-attached");
  return socket;
}

async function attachedPage(url?: string): Promise<FakeWebSocket> {
  renderOperator(url);
  await screen.findByTestId("operator-terminal");
  return attach();
}

function escapeChord(): KeyboardEvent {
  return new KeyboardEvent("keydown", {
    code: "KeyF",
    ctrlKey: true,
    shiftKey: true,
    cancelable: true,
  });
}

beforeEach(() => {
  FakeTerminal.reset();
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

describe("Operator page — Close", () => {
  it("leaves the page by mouse and leaves the session running", async () => {
    const tally = serve([FLEET]);
    const socket = await attachedPage();
    await userEvent.setup().click(screen.getByTestId("operator-close"));
    expect(await screen.findByTestId("home-page")).toBeInTheDocument();
    expect(tally.ends).toBe(0);
    expect(socket.closeCalls).toBe(1);
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
  });

  it("leaves the page by keyboard", async () => {
    serve([FLEET]);
    await attachedPage();
    screen.getByTestId("operator-close").focus();
    await userEvent.setup().keyboard("{Enter}");
    expect(await screen.findByTestId("home-page")).toBeInTheDocument();
  });

  it("leaves the page from the superseded notice", async () => {
    const tally = serve([FLEET]);
    const socket = await attachedPage();
    act(() => {
      socket.serverClose(4001);
    });
    await screen.findByTestId("operator-notice-superseded");
    await userEvent.setup().click(screen.getByTestId("operator-close"));
    expect(await screen.findByTestId("home-page")).toBeInTheDocument();
    expect(tally.ends).toBe(0);
  });

  it("sits outside the terminal area", async () => {
    serve([FLEET]);
    await attachedPage();
    const terminal = screen.getByTestId("operator-terminal");
    expect(terminal.contains(screen.getByTestId("operator-close"))).toBe(false);
    expect(terminal.contains(screen.getByTestId("operator-end"))).toBe(false);
  });

  it("groups the controls and the terminal as two named fieldsets", async () => {
    serve([FLEET]);
    await attachedPage();
    const bar = screen.getByRole("group", { name: "Session controls" });
    expect(bar.tagName).toBe("FIELDSET");
    expect(bar).toContainElement(screen.getByTestId("operator-close"));
    expect(screen.getByRole("group", { name: "Operator terminal" }).tagName).toBe("FIELDSET");
  });
});

describe("Operator page — End session", () => {
  it("asks for confirmation before ending and does nothing until confirmed", async () => {
    const tally = serve([FLEET]);
    await attachedPage();
    await userEvent.setup().click(screen.getByTestId("operator-end"));
    const dialog = await screen.findByTestId("operator-end-dialog");
    expect(within(dialog).getByRole("heading", { name: "End the operator session?" })).toBeInTheDocument();
    expect(tally.ends).toBe(0);
  });

  it("cancels the confirmation without ending anything", async () => {
    const tally = serve([FLEET]);
    await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-end"));
    const dialog = await screen.findByTestId("operator-end-dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("operator-end-dialog")).not.toBeInTheDocument();
    expect(tally.ends).toBe(0);
    expect(screen.getByTestId("operator-terminal")).toBeInTheDocument();
  });

  it("cancels the confirmation on Escape", async () => {
    serve([FLEET]);
    await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-end"));
    const dialog = await screen.findByTestId("operator-end-dialog");
    within(dialog).getByRole("button", { name: "Cancel" }).focus();
    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("operator-end-dialog")).not.toBeInTheDocument();
  });

  it("ends the session by mouse once confirmed and then reports it ended", async () => {
    const tally = serve([FLEET, NONE]);
    await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-end"));
    const dialog = await screen.findByTestId("operator-end-dialog");
    await user.click(within(dialog).getByRole("button", { name: "End session" }));
    expect(await screen.findByTestId("operator-notice-ended")).toBeInTheDocument();
    expect(tally.ends).toBe(1);
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-end-dialog")).not.toBeInTheDocument();
    expect(tally.status).toBe(2);
  });

  it("ends the session by keyboard: Enter opens the confirmation and Ctrl+Enter confirms it", async () => {
    const tally = serve([FLEET, NONE]);
    await attachedPage();
    const user = userEvent.setup();
    screen.getByTestId("operator-end").focus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByTestId("operator-end-dialog");
    within(dialog).getByRole("button", { name: "Cancel" }).focus();
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(await screen.findByTestId("operator-notice-ended")).toBeInTheDocument();
    expect(tally.ends).toBe(1);
  });

  it("marks the End button busy while the session is stopping", async () => {
    const gate = deferredFetch();
    serve([FLEET, NONE]);
    server.use(
      http.delete("/api/operator/session", async () => {
        await gate.settled;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-end"));
    const dialog = await screen.findByTestId("operator-end-dialog");
    await user.click(within(dialog).getByRole("button", { name: "End session" }));
    await waitFor(() => {
      expect(screen.getByTestId("operator-end")).toHaveAttribute("aria-busy", "true");
    });
    gate.release();
    expect(await screen.findByTestId("operator-notice-ended")).toBeInTheDocument();
  });

  it("keeps the terminal and shows the reason when the session would not stop", async () => {
    serve([FLEET]);
    server.use(
      http.delete("/api/operator/session", () =>
        HttpResponse.json({ detail: "the session would not stop" }, { status: 500 }),
      ),
    );
    await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-end"));
    const dialog = await screen.findByTestId("operator-end-dialog");
    await user.click(within(dialog).getByRole("button", { name: "End session" }));
    expect(await screen.findByTestId("operator-end-error")).toHaveTextContent(
      "the session would not stop",
    );
    expect(screen.getByTestId("operator-terminal")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-notice-ended")).not.toBeInTheDocument();
  });

  it("clears the earlier failure when a second End is attempted", async () => {
    let attempts = 0;
    serve([FLEET, NONE]);
    server.use(
      http.delete("/api/operator/session", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "first failure" }, { status: 500 })
          : new HttpResponse(null, { status: 204 });
      }),
    );
    await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-end"));
    await user.click(
      within(await screen.findByTestId("operator-end-dialog")).getByRole("button", {
        name: "End session",
      }),
    );
    await screen.findByTestId("operator-end-error");
    await user.click(screen.getByTestId("operator-end"));
    await user.click(
      within(await screen.findByTestId("operator-end-dialog")).getByRole("button", {
        name: "End session",
      }),
    );
    expect(await screen.findByTestId("operator-notice-ended")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-end-error")).not.toBeInTheDocument();
  });
});

describe("Operator page — leaving the terminal by keyboard", () => {
  it("moves focus from the terminal to the controls on Ctrl+Shift+F", async () => {
    serve([FLEET]);
    await attachedPage();
    expect(screen.getByTestId("operator-close")).not.toHaveFocus();
    act(() => {
      FakeTerminal.latest().pressKey(escapeChord());
    });
    expect(screen.getByTestId("operator-close")).toHaveFocus();
  });

  it("shows the shortcut on screen and speaks it to assistive technology", async () => {
    serve([FLEET]);
    await attachedPage();
    const hint = screen.getByTestId("operator-escape-hint");
    expect(hint).toHaveTextContent("Ctrl Shift F");
    expect(hint).toHaveTextContent("shortcut Control Shift F");
  });

  it("puts Close, End session and Replace session in the tab order ahead of the terminal", async () => {
    serve([FLEET]);
    await attachedPage();
    const user = userEvent.setup();
    screen.getByTestId("operator-close").focus();
    await user.tab();
    expect(screen.getByTestId("operator-end")).toHaveFocus();
    await user.tab();
    expect(screen.getByTestId("operator-replace")).toHaveFocus();
  });
});

describe("Operator page — starting a session", () => {
  it("starts the chosen scope from the picker and then attaches to the new session", async () => {
    const tally = serve([NONE, FLEET]);
    serveRepos("acme/widget", "acme/gadget");
    renderOperator();
    const user = userEvent.setup();
    await screen.findByTestId("operator-scope-dialog");
    await user.click(screen.getByTestId("operator-scope-repo"));
    await user.selectOptions(await enabledTarget(), "acme/widget");
    await user.click(screen.getByTestId("operator-start-submit"));
    expect(await screen.findByTestId("operator-terminal")).toBeInTheDocument();
    expect(tally.starts).toEqual([{ scope: "repo", target: "acme/widget", replace: false }]);
    expect(screen.queryByTestId("operator-scope-dialog")).not.toBeInTheDocument();
    await attach();
  });

  it("keeps the picker closed after Cancel and reopens it from the Start button", async () => {
    serve([NONE]);
    renderOperator();
    const user = userEvent.setup();
    const dialog = await screen.findByTestId("operator-scope-dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("operator-scope-dialog")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("operator-start"));
    expect(await screen.findByTestId("operator-scope-dialog")).toBeInTheDocument();
  });

  it("offers to start a new session from the ended notice, with nothing to replace", async () => {
    serve([FLEET, NONE]);
    await attachedPage();
    act(() => {
      FakeWebSocket.latest().serverClose(4002);
    });
    await screen.findByTestId("operator-notice-ended");
    await waitFor(() => {
      expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
    });
    await userEvent.setup().click(screen.getByTestId("operator-start"));
    expect(await screen.findByTestId("operator-scope-dialog")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-replace-confirm")).not.toBeInTheDocument();
  });

  it("names the unavailable cause when a session started after an End then disappears", async () => {
    serve([FLEET, NONE, REPLACEMENT, BLOCKED]);
    await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-end"));
    await user.click(
      within(await screen.findByTestId("operator-end-dialog")).getByRole("button", {
        name: "End session",
      }),
    );
    await screen.findByTestId("operator-notice-ended");
    await user.click(screen.getByTestId("operator-start"));
    await screen.findByTestId("operator-scope-dialog");
    await user.click(screen.getByTestId("operator-start-submit"));
    await attach(1);
    act(() => {
      FakeWebSocket.latest().serverClose(4404);
    });
    expect(await screen.findByTestId("operator-unavailable-cause")).toHaveTextContent(
      "tmux is not installed",
    );
    expect(screen.queryByTestId("operator-notice-ended")).not.toBeInTheDocument();
  });
});

describe("Operator page — replacing a running session", () => {
  it("needs an explicit replace, then attaches to the replacement and drops the old attachment", async () => {
    const tally = serve([FLEET, REPLACEMENT]);
    serveStuckWorkspace("fix-it");
    const first = await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-replace"));
    await screen.findByTestId("operator-scope-dialog");
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
    await user.click(screen.getByTestId("operator-replace-confirm"));
    await user.click(screen.getByTestId("operator-scope-workspace"));
    await user.selectOptions(await enabledTarget(), "fix-it");
    await user.click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(screen.queryByTestId("operator-scope-dialog")).not.toBeInTheDocument();
    });
    expect(tally.starts).toEqual([{ scope: "workspace", target: "fix-it", replace: true }]);
    await waitFor(() => {
      expect(FakeWebSocket.instances).toHaveLength(2);
    });
    expect(first.closeCalls).toBe(1);
    expect(screen.getByTestId("operator-summary")).toHaveTextContent("Workspace session · fix-it");
  });

  it("gives the replacement a fresh terminal instead of the replaced session's screen", async () => {
    serve([FLEET, REPLACEMENT]);
    await attachedPage();
    const previous = FakeTerminal.latest();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-replace"));
    await screen.findByTestId("operator-scope-dialog");
    await user.click(screen.getByTestId("operator-replace-confirm"));
    await user.click(screen.getByTestId("operator-start-submit"));
    await waitFor(() => {
      expect(FakeTerminal.instances).toHaveLength(2);
    });
    expect(previous.disposed).toBe(true);
    expect(FakeTerminal.latest()).not.toBe(previous);
    expect(FakeTerminal.latest().disposed).toBe(false);
  });

  it("keeps the running session untouched when the replace dialog is cancelled", async () => {
    const tally = serve([FLEET]);
    const socket = await attachedPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("operator-replace"));
    const dialog = await screen.findByTestId("operator-scope-dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("operator-scope-dialog")).not.toBeInTheDocument();
    expect(tally.starts).toEqual([]);
    expect(socket.closeCalls).toBe(0);
    expect(screen.getByTestId("operator-terminal")).toBeInTheDocument();
  });
});

describe("Operator page — the workspace preset link", () => {
  it("opens the picker already scoped to the workspace when no session is running", async () => {
    serve([NONE]);
    serveStuckWorkspace("fix-it");
    renderOperator("/operator?scope=workspace&target=fix-it");
    await screen.findByTestId("operator-scope-dialog");
    expect(screen.getByTestId("operator-scope-workspace")).toBeChecked();
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("fix-it");
    });
  });

  it("keeps a linked workspace selected even when the service no longer lists it as stuck", async () => {
    serve([NONE]);
    server.use(http.get("/api/workspaces", () => HttpResponse.json([])));
    renderOperator("/operator?scope=workspace&target=fix-it");
    await screen.findByTestId("operator-scope-dialog");
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("fix-it");
    });
    expect(screen.getByTestId("operator-start-submit")).toBeEnabled();
  });

  it("opens the picker in replace mode over a live session, and keeps that session if cancelled", async () => {
    const tally = serve([FLEET]);
    serveStuckWorkspace("fix-it");
    renderOperator("/operator?scope=workspace&target=fix-it");
    const dialog = await screen.findByTestId("operator-scope-dialog");
    expect(screen.getByTestId("operator-scope-workspace")).toBeChecked();
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("fix-it");
    });
    expect(screen.getByTestId("operator-replace-notice")).toBeInTheDocument();
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("operator-scope-dialog")).not.toBeInTheDocument();
    expect(tally.starts).toEqual([]);
    expect(await screen.findByTestId("operator-terminal")).toBeInTheDocument();
    await attach();
    expect(screen.queryByTestId("operator-scope-dialog")).not.toBeInTheDocument();
  });

  it("ignores a scope it does not recognise and just attaches to the live session", async () => {
    serve([FLEET]);
    renderOperator("/operator?scope=galaxy&target=fix-it");
    await screen.findByTestId("operator-terminal");
    await attach();
    expect(screen.queryByTestId("operator-scope-dialog")).not.toBeInTheDocument();
  });

  it("opens the picker on the named scope with nothing selected when the link names no target and several repositories are connected", async () => {
    serve([NONE]);
    serveRepos("acme/widget", "acme/gadget");
    renderOperator("/operator?scope=repo");
    await screen.findByTestId("operator-scope-dialog");
    expect(screen.getByTestId("operator-scope-repo")).toBeChecked();
    expect(await enabledTarget()).toHaveValue("");
    expect(screen.getByTestId("operator-start-submit")).toBeDisabled();
  });

  it("preselects the only repository when the link names the repository scope on a single-repository install", async () => {
    serve([NONE]);
    serveRepos("acme/widget");
    renderOperator("/operator?scope=repo");
    await screen.findByTestId("operator-scope-dialog");
    await waitFor(() => {
      expect(screen.getByTestId("operator-target")).toHaveValue("acme/widget");
    });
  });

  it("opens the picker on the fleet scope when there is no link at all", async () => {
    serve([NONE]);
    renderOperator();
    await screen.findByTestId("operator-scope-dialog");
    expect(screen.getByTestId("operator-scope-fleet")).toBeChecked();
  });
});
