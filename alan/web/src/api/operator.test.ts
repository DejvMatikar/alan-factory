import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import { server } from "../test/server";
import {
  endOperatorSession,
  fetchAttachTicket,
  getOperatorStatus,
  operatorSocketUrl,
  startOperatorSession,
} from "./operator";

const SESSION = {
  scope: "repo",
  target: "acme/widget",
  started_at: "2026-09-29T10:00:00Z",
  update_warning: null,
  exited: false,
};

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the call to reject");
}

describe("getOperatorStatus", () => {
  it("returns the status body", async () => {
    server.use(
      http.get("/api/operator/status", () =>
        HttpResponse.json({ session: SESSION, unavailable: null }),
      ),
    );
    await expect(getOperatorStatus()).resolves.toEqual({ session: SESSION, unavailable: null });
  });

  it("rejects with an ApiError carrying the status and a string detail", async () => {
    server.use(
      http.get("/api/operator/status", () =>
        HttpResponse.json({ detail: "the operator terminal is broken" }, { status: 500 }),
      ),
    );
    const error = await rejectionOf(getOperatorStatus());
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 500, detail: "the operator terminal is broken" });
  });
});

describe("startOperatorSession", () => {
  it("posts the scope, target and replace flag as JSON and returns the session", async () => {
    const seen: unknown[] = [];
    server.use(
      http.post("/api/operator/session", async ({ request }) => {
        seen.push(await request.json());
        return HttpResponse.json(SESSION);
      }),
    );
    const body = { scope: "repo", target: "acme/widget", replace: true } as const;
    await expect(startOperatorSession(body)).resolves.toEqual(SESSION);
    expect(seen).toEqual([body]);
  });

  it("surfaces the exact unavailable sentence from the nested 503 detail", async () => {
    server.use(
      http.post("/api/operator/session", () =>
        HttpResponse.json(
          { detail: { reason: "tmux_missing", detail: "tmux is not installed." } },
          { status: 503 },
        ),
      ),
    );
    const error = await rejectionOf(startOperatorSession({ scope: "fleet", replace: false }));
    expect(error).toMatchObject({ status: 503, detail: "tmux is not installed." });
  });

  it("falls back to the status text when the body carries no detail", async () => {
    server.use(
      http.post("/api/operator/session", () =>
        HttpResponse.json({ unexpected: true }, { status: 409, statusText: "Conflict" }),
      ),
    );
    const error = await rejectionOf(startOperatorSession({ scope: "fleet", replace: false }));
    expect(error).toMatchObject({ status: 409, detail: "Conflict" });
  });

  it("falls back to the status text when a nested detail is not a string", async () => {
    server.use(
      http.post("/api/operator/session", () =>
        HttpResponse.json({ detail: { detail: 42 } }, { status: 422, statusText: "Unprocessable" }),
      ),
    );
    const error = await rejectionOf(startOperatorSession({ scope: "fleet", replace: false }));
    expect(error).toMatchObject({ status: 422, detail: "Unprocessable" });
  });

  it("falls back to the status text when the error response has no body at all", async () => {
    server.use(
      http.post("/api/operator/session", () =>
        new HttpResponse(null, { status: 500, statusText: "Server Error" }),
      ),
    );
    const error = await rejectionOf(startOperatorSession({ scope: "fleet", replace: false }));
    expect(error).toMatchObject({ status: 500, detail: "Server Error" });
  });
});

describe("endOperatorSession", () => {
  it("resolves on the 204 that ends the session", async () => {
    server.use(
      http.delete("/api/operator/session", () => new HttpResponse(null, { status: 204 })),
    );
    await expect(endOperatorSession()).resolves.toBeUndefined();
  });

  it("rejects when no session is live", async () => {
    server.use(
      http.delete("/api/operator/session", () =>
        HttpResponse.json({ detail: "no operator session" }, { status: 404 }),
      ),
    );
    const error = await rejectionOf(endOperatorSession());
    expect(error).toMatchObject({ status: 404, detail: "no operator session" });
  });
});

describe("fetchAttachTicket", () => {
  it("returns only the ticket string", async () => {
    server.use(
      http.post("/api/operator/attach-ticket", () =>
        HttpResponse.json({ ticket: "tk-1", expires_in_s: 30 }),
      ),
    );
    await expect(fetchAttachTicket()).resolves.toBe("tk-1");
  });

  it("rejects with a 404 ApiError when no session is live", async () => {
    server.use(
      http.post("/api/operator/attach-ticket", () =>
        HttpResponse.json({ detail: "no operator session" }, { status: 404 }),
      ),
    );
    const error = await rejectionOf(fetchAttachTicket());
    expect(error).toMatchObject({ status: 404 });
  });
});

describe("operatorSocketUrl", () => {
  it("targets the terminal socket over ws on an http page and carries the ticket", () => {
    const url = new URL(operatorSocketUrl("tk 1", null));
    expect(url.protocol).toBe("ws:");
    expect(url.pathname).toBe("/api/operator/terminal");
    expect(url.searchParams.get("ticket")).toBe("tk 1");
    expect(url.searchParams.has("cols")).toBe(false);
    expect(url.searchParams.has("rows")).toBe(false);
  });

  it("carries the size when one is known", () => {
    const url = new URL(operatorSocketUrl("tk-1", { cols: 120, rows: 40 }));
    expect(url.searchParams.get("cols")).toBe("120");
    expect(url.searchParams.get("rows")).toBe("40");
  });

  it("uses wss on an https page", () => {
    vi.stubGlobal("location", new URL("https://alan.example/operator"));
    expect(operatorSocketUrl("tk-1", null)).toBe(
      "wss://alan.example/api/operator/terminal?ticket=tk-1",
    );
  });
});
