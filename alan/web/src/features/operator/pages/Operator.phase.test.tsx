import { act, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http, type JsonBodyType } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { deferredFetch } from "../../../test/docReadHarness";
import { FakeTerminal } from "../../../test/fakeXterm";
import { FakeWebSocket } from "../../../test/fakeWebSocket";
import { server } from "../../../test/server";
import Operator from "./Operator";

vi.mock("@xterm/xterm", async () => {
  const { FakeTerminal: Terminal } = await import("../../../test/fakeXterm");
  return { Terminal };
});

vi.mock("@xterm/addon-fit", async () => {
  const { FakeFitAddon: FitAddon } = await import("../../../test/fakeXterm");
  return { FitAddon };
});

const LIVE = {
  session: {
    scope: "repo",
    target: "acme/widget",
    started_at: "2026-09-29T10:00:00Z",
    update_warning: null,
    exited: false,
  },
  unavailable: null,
};
const NONE = { session: null, unavailable: null };

function unavailable(reason: string, detail: string) {
  return { session: null, unavailable: { reason, detail } };
}

function serveStatus(steps: JsonBodyType[]) {
  const tally = { calls: 0 };
  server.use(
    http.get("/api/operator/status", () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)];
      tally.calls += 1;
      return step === "error" ? HttpResponse.error() : HttpResponse.json(step);
    }),
  );
  return tally;
}

function serveTickets() {
  const tally = { calls: 0 };
  server.use(
    http.post("/api/operator/attach-ticket", () => {
      tally.calls += 1;
      return HttpResponse.json({ ticket: `tk-${String(tally.calls)}`, expires_in_s: 30 });
    }),
  );
  return tally;
}

function stubViewport(wide: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

function renderOperator(url = "/operator") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/operator" element={<Operator />} />
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
  return socket;
}

beforeEach(() => {
  FakeTerminal.reset();
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  serveTickets();
});

describe("Operator page — before a session", () => {
  it("shows a checking message and nothing else while the status loads", async () => {
    const gate = deferredFetch();
    server.use(
      http.get("/api/operator/status", async () => {
        await gate.settled;
        return HttpResponse.json(NONE);
      }),
    );
    renderOperator();
    expect(await screen.findByTestId("operator-notice-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    gate.release();
    expect(await screen.findByTestId("operator-notice-choose")).toBeInTheDocument();
  });

  it("names an unreachable service when the status request fails", async () => {
    serveStatus(["error"]);
    renderOperator();
    expect(await screen.findByTestId("operator-unavailable-cause")).toHaveTextContent(
      "The service did not answer the status request",
    );
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
  });

  it.each([
    ["detached_mode_off", "Detached worker mode is off"],
    ["tmux_missing", "tmux is not installed"],
    ["claude_missing", "The claude CLI is not installed"],
    ["git_missing", "git is not installed"],
    ["checkout_clone_failed", "The operator checkout could not be cloned"],
  ])("names the %s cause instead of showing a terminal", async (reason, label) => {
    serveStatus([unavailable(reason, `server detail for ${reason}`)]);
    renderOperator();
    expect(await screen.findByTestId("operator-unavailable-cause")).toHaveTextContent(label);
    expect(screen.getByTestId("operator-unavailable-detail")).toHaveTextContent(
      `server detail for ${reason}`,
    );
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(FakeTerminal.instances).toHaveLength(0);
  });

  it("opens the scope picker when nothing is running and nothing is missing", async () => {
    serveStatus([NONE]);
    renderOperator();
    expect(await screen.findByTestId("operator-scope-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("operator-notice-choose")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
  });

  it("asks for a larger screen at phone width and opens nothing", async () => {
    stubViewport(false);
    const tickets = serveTickets();
    serveStatus([LIVE]);
    renderOperator();
    expect(await screen.findByTestId("operator-phone-notice")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(tickets.calls).toBe(0);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("asks for a larger screen at phone width even when no session is running", async () => {
    stubViewport(false);
    serveStatus([NONE]);
    renderOperator();
    expect(await screen.findByTestId("operator-phone-notice")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders the page heading as a level-one heading", async () => {
    serveStatus([NONE]);
    renderOperator();
    expect(await screen.findByRole("heading", { level: 1, name: "Operator" })).toBeInTheDocument();
  });
});

describe("Operator page — with a live session", () => {
  it("reattaches on page open without any user action, through connecting to attached", async () => {
    serveStatus([LIVE]);
    const tickets = serveTickets();
    renderOperator();
    expect(await screen.findByTestId("operator-terminal")).toBeInTheDocument();
    expect(await screen.findByTestId("operator-notice-connecting")).toBeInTheDocument();
    const socket = await attach();
    expect(tickets.calls).toBe(1);
    expect(new URL(socket.url).searchParams.get("ticket")).toBe("tk-1");
    expect(await screen.findByTestId("operator-notice-attached")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("writes the session's output into the terminal", async () => {
    serveStatus([LIVE]);
    renderOperator();
    const socket = await attach();
    await screen.findByTestId("operator-terminal");
    socket.serverSend(new TextEncoder().encode("claude is ready").buffer);
    expect(FakeTerminal.latest().writtenText()).toBe("claude is ready");
  });

  it("attaches to a session whose start time was never recorded", async () => {
    serveStatus([{ ...LIVE, session: { ...LIVE.session, started_at: null } }]);
    renderOperator();
    await screen.findByTestId("operator-terminal");
    await attach();
    expect(await screen.findByTestId("operator-notice-attached")).toBeInTheDocument();
  });

  it("attaches to the replacement when one session with no recorded start replaces another", async () => {
    const unrecorded = (scope: string, target: string | null) => ({
      ...LIVE,
      session: { ...LIVE.session, scope, target, started_at: null },
    });
    serveStatus([unrecorded("repo", "acme/widget"), unrecorded("fleet", null)]);
    renderOperator();
    const first = await attach();
    act(() => {
      first.serverClose(4002);
    });
    await waitFor(() => {
      expect(FakeWebSocket.instances).toHaveLength(2);
    });
    expect(await screen.findByTestId("operator-summary")).toHaveTextContent("Global session");
  });

  it("describes the running session in the controls bar", async () => {
    serveStatus([LIVE]);
    renderOperator();
    expect(await screen.findByTestId("operator-summary")).toHaveTextContent(
      "Repository session · acme/widget",
    );
  });

  it("says so when the checkout could not be updated, and still shows the terminal", async () => {
    serveStatus([
      {
        ...LIVE,
        session: { ...LIVE.session, update_warning: "fetch failed: network unreachable" },
      },
    ]);
    renderOperator();
    expect(await screen.findByTestId("operator-update-warning")).toHaveTextContent(
      "fetch failed: network unreachable",
    );
    expect(screen.getByTestId("operator-terminal")).toBeInTheDocument();
  });

  it("shows no update warning when the checkout was updated", async () => {
    serveStatus([LIVE]);
    renderOperator();
    await screen.findByTestId("operator-terminal");
    expect(screen.queryByTestId("operator-update-warning")).not.toBeInTheDocument();
  });

  it("keeps the same terminal on screen while it reattaches after a lost connection", async () => {
    serveStatus([LIVE]);
    renderOperator();
    const socket = await attach();
    await screen.findByTestId("operator-terminal");
    const terminal = FakeTerminal.latest();
    act(() => {
      socket.serverClose(1006);
    });
    expect(await screen.findByTestId("operator-notice-reattaching")).toBeInTheDocument();
    expect(screen.getByTestId("operator-terminal")).toBeInTheDocument();
    expect(terminal.disposed).toBe(false);
    expect(FakeTerminal.instances).toHaveLength(1);
  });

  it("tells an older view it was superseded, removes its terminal, and does not reconnect", async () => {
    serveStatus([LIVE]);
    const tickets = serveTickets();
    renderOperator();
    const socket = await attach();
    await screen.findByTestId("operator-terminal");
    const terminal = FakeTerminal.latest();
    act(() => {
      socket.serverClose(4001);
    });
    expect(await screen.findByTestId("operator-notice-superseded")).toHaveTextContent(
      "taken over by another browser window",
    );
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
    expect(terminal.disposed).toBe(true);
    expect(tickets.calls).toBe(1);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("keeps Close, End session and Replace session on screen once the view was superseded", async () => {
    serveStatus([LIVE]);
    renderOperator();
    const socket = await attach();
    await screen.findByTestId("operator-terminal");
    act(() => {
      socket.serverClose(4001);
    });
    await screen.findByTestId("operator-notice-superseded");
    expect(screen.getByTestId("operator-close")).toBeInTheDocument();
    expect(screen.getByTestId("operator-end")).toBeInTheDocument();
    expect(screen.getByTestId("operator-replace")).toBeInTheDocument();
    expect(screen.getByTestId("operator-summary")).toHaveTextContent("Repository session · acme/widget");
  });

  it("says the session ended when the socket closes with 4002, and keeps saying so once the status catches up", async () => {
    const status = serveStatus([LIVE, NONE]);
    renderOperator();
    const socket = await attach();
    act(() => {
      socket.serverClose(4002);
    });
    expect(await screen.findByTestId("operator-notice-ended")).toBeInTheDocument();
    await waitFor(() => {
      expect(status.calls).toBe(2);
    });
    expect(screen.getByTestId("operator-notice-ended")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("drops the session controls as soon as the socket reports the end, before the status catches up", async () => {
    serveStatus([LIVE]);
    renderOperator();
    const socket = await attach();
    await screen.findByTestId("operator-bar");
    act(() => {
      socket.serverClose(4002);
    });
    expect(await screen.findByTestId("operator-notice-ended")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-bar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
  });

  it("refetches the status on 4404 and falls back to the scope picker when no session remains", async () => {
    const status = serveStatus([LIVE, NONE]);
    renderOperator();
    const socket = await attach();
    act(() => {
      socket.serverClose(4404);
    });
    expect(await screen.findByTestId("operator-scope-dialog")).toBeInTheDocument();
    expect(status.calls).toBe(2);
    expect(screen.getByTestId("operator-notice-choose")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
  });

  it("names the refused connection after the fresh-ticket retry is rejected too", async () => {
    serveStatus([LIVE]);
    renderOperator();
    await attach();
    act(() => {
      FakeWebSocket.latest().serverClose(4401);
    });
    await waitFor(() => {
      expect(FakeWebSocket.instances).toHaveLength(2);
    });
    act(() => {
      FakeWebSocket.latest().serverClose(4401);
    });
    expect(await screen.findByTestId("operator-unavailable-cause")).toHaveTextContent(
      "The terminal connection was refused",
    );
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
    expect(screen.getByTestId("operator-close")).toBeInTheDocument();
    expect(screen.getByTestId("operator-end")).toBeInTheDocument();
  });

  it("closes the attach socket but not the session when the page is left", async () => {
    serveStatus([LIVE]);
    const { unmount } = renderOperator();
    const socket = await attach();
    await screen.findByTestId("operator-terminal");
    unmount();
    expect(socket.closeCalls).toBe(1);
    expect(FakeTerminal.latest().disposed).toBe(true);
  });
});
