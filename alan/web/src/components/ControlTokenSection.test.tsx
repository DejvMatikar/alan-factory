import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "../controlToken";
import { server } from "../test/server";
import ControlTokenSection, { remainingLifetimeText } from "./ControlTokenSection";

function Harness({ initialDraft = "" }: Readonly<{ initialDraft?: string }>) {
  const [draft, setDraft] = useState(initialDraft);
  const [showToken, setShowToken] = useState(false);
  const [tokenSet, setTokenSet] = useState(initialDraft.length > 0);
  return (
    <ControlTokenSection
      draft={draft}
      showToken={showToken}
      tokenSet={tokenSet}
      onDraftChange={setDraft}
      onToggleShowToken={() => setShowToken((prev) => !prev)}
      onSave={() => setTokenSet(draft.length > 0)}
      onClear={() => {
        setDraft("");
        setTokenSet(false);
      }}
    />
  );
}

function statusResponse(state: "active" | "expired" | "revoked", deadline: string | null) {
  return HttpResponse.json({ state, deadline });
}

describe("remainingLifetimeText", () => {
  it("returns null when there is no deadline", () => {
    expect(remainingLifetimeText(null, 1_000)).toBeNull();
  });

  it("returns null when there is no server clock reading", () => {
    expect(remainingLifetimeText("2026-09-14T00:00:00Z", null)).toBeNull();
  });

  it("returns null for an unparseable deadline", () => {
    expect(remainingLifetimeText("not-a-date", 1_000)).toBeNull();
  });

  it("returns null once the deadline has already passed", () => {
    const now = Date.parse("2026-09-15T00:00:00Z");
    expect(remainingLifetimeText("2026-09-14T00:00:00Z", now)).toBeNull();
  });

  it("says 'expires soon' with under a day remaining", () => {
    const now = Date.parse("2026-09-13T20:00:00Z");
    expect(remainingLifetimeText("2026-09-14T00:00:00Z", now)).toBe("expires soon");
  });

  it("uses singular 'day' at exactly one day remaining", () => {
    const now = Date.parse("2026-09-13T00:00:00Z");
    expect(remainingLifetimeText("2026-09-14T00:00:00Z", now)).toBe("expires in 1 day");
  });

  it("uses plural 'days' for multiple days remaining", () => {
    const now = Date.parse("2026-08-15T00:00:00Z");
    expect(remainingLifetimeText("2026-09-14T00:00:00Z", now)).toBe("expires in 30 days");
  });
});

describe("ControlTokenSection", () => {
  it("renders the local paste line and the server lifecycle line as two separate lines", async () => {
    server.use(http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")));
    render(<Harness />);
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("No token set.");
    const serverLine = screen.getByTestId("control-token-server-status");
    await waitFor(() => {
      expect(serverLine).toHaveTextContent(/^Server token: active, expires/);
    });
    expect(serverLine).not.toHaveTextContent("2026-09-14T00:00:00+00:00");
    expect(screen.getByTestId("control-token-status")).not.toBe(
      screen.getByTestId("control-token-server-status"),
    );
  });

  it("shows a day-granularity countdown computed from the response's own Date header, not the browser clock", async () => {
    server.use(
      http.get("/api/control-token/status", () =>
        HttpResponse.json(
          { state: "active", deadline: "2026-09-14T00:00:00Z" },
          { headers: { Date: new Date("2026-08-15T00:00:00Z").toUTCString() } },
        ),
      ),
    );
    render(<Harness />);
    await waitFor(() => {
      expect(screen.getByTestId("control-token-server-status")).toHaveTextContent(
        "Server token: active, expires in 30 days",
      );
    });
  });

  it("treats an unparseable Date header as no server clock reading, falling back to a plain deadline", async () => {
    server.use(
      http.get("/api/control-token/status", () =>
        HttpResponse.json(
          { state: "active", deadline: "2026-09-14T00:00:00Z" },
          { headers: { Date: "not-a-real-http-date" } },
        ),
      ),
    );
    render(<Harness />);
    const serverLine = screen.getByTestId("control-token-server-status");
    await waitFor(() => {
      expect(serverLine).toHaveTextContent(/^Server token: active, expires/);
    });
    expect(serverLine).not.toHaveTextContent("expires in");
  });

  it("keeps the local status pill's exact existing strings for a set token", () => {
    render(<Harness initialDraft="existing-token" />);
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("Token set.");
  });

  it("shows an unreachable-server message when the status fetch itself fails", async () => {
    server.use(http.get("/api/control-token/status", () => HttpResponse.error()));
    render(<Harness />);
    await waitFor(() => {
      expect(screen.getByTestId("control-token-server-status")).toHaveTextContent(
        "Could not reach the server",
      );
    });
  });

  it("falls back to 'unknown' when an expired status carries no deadline", async () => {
    server.use(http.get("/api/control-token/status", () => statusResponse("expired", null)));
    render(<Harness />);
    await waitFor(() => {
      expect(screen.getByTestId("control-token-server-status")).toHaveTextContent(
        "expired (deadline was unknown)",
      );
    });
  });

  it("shows a checking placeholder before the first status fetch resolves, then the expired copy", async () => {
    server.use(
      http.get("/api/control-token/status", () => statusResponse("expired", "2026-07-01T00:00:00+00:00")),
    );
    render(<Harness />);
    await waitFor(() => {
      expect(screen.getByTestId("control-token-server-status")).toHaveTextContent(/expired/);
    });
    expect(screen.getByTestId("control-token-server-status")).toHaveTextContent(
      "paste the new value into every browser",
    );
  });

  it("shows the revoked copy distinctly from active/expired", async () => {
    server.use(http.get("/api/control-token/status", () => statusResponse("revoked", null)));
    render(<Harness />);
    await waitFor(() => {
      expect(screen.getByTestId("control-token-server-status")).toHaveTextContent(/revoked/);
    });
  });

  it("local paste controls (input, toggle, save, clear) still work unchanged", async () => {
    server.use(http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")));
    render(<Harness />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("control-token-input"), "new-token");
    await user.click(screen.getByTestId("save-token"));
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("Token set.");
    await user.click(screen.getByTestId("clear-token"));
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("No token set.");
    expect(screen.getByTestId("control-token-input")).toHaveValue("");
  });

  it("Extend sends no body, leaves the draft untouched, and updates the server line with the new deadline", async () => {
    let extendCalls = 0;
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/extend", async ({ request }) => {
        extendCalls += 1;
        expect(await request.text()).toBe("");
        return statusResponse("active", "2026-10-14T00:00:00+00:00");
      }),
    );
    render(<Harness initialDraft="existing-token" />);
    const serverLine = screen.getByTestId("control-token-server-status");
    await waitFor(() => {
      expect(serverLine).toHaveTextContent(/^Server token: active, expires/);
    });
    const before = serverLine.textContent;
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-extend"));
    await waitFor(() => {
      expect(serverLine.textContent).not.toBe(before);
    });
    expect(extendCalls).toBe(1);
    expect(screen.getByTestId("control-token-input")).toHaveValue("existing-token");
    expect(screen.getByTestId("control-token-announcement")).toHaveTextContent(/extended/);
  });

  it("Extend attaches the browser's stored control token — unlike the unauthenticated status GET", async () => {
    writeControlToken("stored-secret");
    let receivedHeader: string | null = null;
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/extend", ({ request }) => {
        receivedHeader = request.headers.get(CONTROL_TOKEN_HEADER);
        return statusResponse("active", "2026-10-14T00:00:00+00:00");
      }),
    );
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-extend"));
    await waitFor(() => {
      expect(receivedHeader).toBe("stored-secret");
    });
    clearControlToken();
  });

  it("Extend's announcement falls back to 'unknown' when the response carries no deadline", async () => {
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/extend", () => statusResponse("active", null)),
    );
    render(<Harness initialDraft="existing-token" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-extend"));
    await waitFor(() => {
      expect(screen.getByTestId("control-token-announcement")).toHaveTextContent(
        "now expires unknown",
      );
    });
  });

  it("Replace opens a reset-variant confirm dialog stating the out-of-band fetch and the multi-device sign-out, and cancel makes no request", async () => {
    let rotateCalls = 0;
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/rotate", () => {
        rotateCalls += 1;
        return statusResponse("active", "2026-10-14T00:00:00+00:00");
      }),
    );
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-replace"));
    const dialog = screen.getByTestId("control-token-replace-confirm");
    expect(dialog).toHaveTextContent(/never sent over the network/);
    expect(dialog).toHaveTextContent(/including this browser/);
    expect(within(dialog).getByRole("button", { name: "Replace token" }).className).toContain(
      "bg-btn-reset",
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("control-token-replace-confirm")).not.toBeInTheDocument();
    expect(rotateCalls).toBe(0);
  });

  it("Replace, once confirmed, rotates the server token and announces the multi-device sign-out", async () => {
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/rotate", () => statusResponse("active", "2026-10-14T00:00:00+00:00")),
    );
    render(<Harness />);
    const serverLine = screen.getByTestId("control-token-server-status");
    await waitFor(() => {
      expect(serverLine).toHaveTextContent(/^Server token: active, expires/);
    });
    const before = serverLine.textContent;
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-replace"));
    await user.click(
      within(screen.getByTestId("control-token-replace-confirm")).getByRole("button", {
        name: "Replace token",
      }),
    );
    await waitFor(() => {
      expect(serverLine.textContent).not.toBe(before);
    });
    expect(screen.getByTestId("control-token-announcement")).toHaveTextContent(/signed out/);
  });

  it("Revoke: Cancel closes the dialog and makes no request", async () => {
    let revokeCalls = 0;
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/revoke", () => {
        revokeCalls += 1;
        return statusResponse("revoked", null);
      }),
    );
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-revoke"));
    const dialog = screen.getByTestId("control-token-revoke-confirm");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("control-token-revoke-confirm")).not.toBeInTheDocument();
    expect(revokeCalls).toBe(0);
  });

  it("Revoke is framed as destructive (destroy-red confirm) and, once confirmed, leaves no replacement", async () => {
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/revoke", () => statusResponse("revoked", null)),
    );
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-revoke"));
    const dialog = screen.getByTestId("control-token-revoke-confirm");
    expect(dialog).toHaveTextContent(/no replacement/);
    expect(dialog).toHaveTextContent(/including this one/);
    expect(within(dialog).getByRole("button", { name: "Revoke token" }).className).toContain(
      "bg-btn-destroy",
    );
    await user.click(within(dialog).getByRole("button", { name: "Revoke token" }));
    await waitFor(() => {
      expect(screen.getByTestId("control-token-server-status")).toHaveTextContent(/revoked/);
    });
    expect(screen.getByTestId("control-token-announcement")).toHaveTextContent(/No replacement exists/);
  });

  it("a mutation blocked by a missing token names the cause and points at Settings", async () => {
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/extend", () =>
        HttpResponse.json({ detail: "control token required" }, { status: 401 }),
      ),
    );
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-extend"));
    await waitFor(() => {
      expect(screen.getByTestId("control-token-action-error-message")).toHaveTextContent(
        "Enter your control token in Settings",
      );
    });
  });

  it("a mutation blocked by an expired token names that distinct cause and remedy", async () => {
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/extend", () =>
        HttpResponse.json({ detail: "control token expired" }, { status: 401 }),
      ),
    );
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-extend"));
    await waitFor(() => {
      expect(screen.getByTestId("control-token-action-error-message")).toHaveTextContent(
        /expired.*extend or replace/,
      );
    });
  });

  it("shows each mutation's response without issuing another status GET", async () => {
    let statusGets = 0;
    server.use(
      http.get("/api/control-token/status", () => {
        statusGets += 1;
        return statusResponse("active", "2026-09-14T00:00:00+00:00");
      }),
      http.post("/api/control-token/extend", () => statusResponse("active", "2026-10-14T00:00:00+00:00")),
      http.post("/api/control-token/rotate", () => statusResponse("active", "2026-11-14T00:00:00+00:00")),
      http.post("/api/control-token/revoke", () => statusResponse("revoked", null)),
    );
    render(<Harness />);
    const serverLine = screen.getByTestId("control-token-server-status");
    await waitFor(() => {
      expect(serverLine).toHaveTextContent(/^Server token: active, expires/);
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-extend"));
    await waitFor(() => {
      expect(screen.getByTestId("control-token-announcement")).toHaveTextContent(/extended/);
    });
    await user.click(screen.getByTestId("control-token-replace"));
    await user.click(
      within(screen.getByTestId("control-token-replace-confirm")).getByRole("button", {
        name: "Replace token",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("control-token-announcement")).toHaveTextContent(/signed out/);
    });
    await user.click(screen.getByTestId("control-token-revoke"));
    await user.click(
      within(screen.getByTestId("control-token-revoke-confirm")).getByRole("button", {
        name: "Revoke token",
      }),
    );
    await waitFor(() => {
      expect(serverLine).toHaveTextContent(/revoked/);
    });
    expect(statusGets).toBe(1);
  });

  it("buttons disable while an action is in flight, showing a busy label", async () => {
    server.use(
      http.get("/api/control-token/status", () => statusResponse("active", "2026-09-14T00:00:00+00:00")),
      http.post("/api/control-token/extend", () => new Promise<never>(() => undefined)),
    );
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("control-token-extend"));
    await waitFor(() => {
      expect(screen.getByTestId("control-token-extend")).toBeDisabled();
    });
    expect(screen.getByTestId("control-token-extend")).toHaveTextContent("Extending…");
    expect(screen.getByTestId("control-token-replace")).toBeDisabled();
    expect(screen.getByTestId("control-token-revoke")).toBeDisabled();
  });
});
