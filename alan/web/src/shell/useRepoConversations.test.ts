import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { createElement, Fragment, type ReactNode } from "react";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi, type MockInstance } from "vitest";

const poll = vi.hoisted(() => ({ ms: 15000 }));

vi.mock("../api/ServerEventsProvider", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  get FALLBACK_POLL_MS() {
    return poll.ms;
  },
}));

import { server } from "../test/server";
import { useRepoConversations } from "./useRepoConversations";

let visibilitySpy: MockInstance<() => DocumentVisibilityState> | null = null;

function setVisibility(state: DocumentVisibilityState): void {
  visibilitySpy?.mockRestore();
  visibilitySpy = vi.spyOn(document, "visibilityState", "get").mockReturnValue(state);
}

afterEach(() => {
  visibilitySpy?.mockRestore();
  visibilitySpy = null;
  poll.ms = 15000;
});

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function Harness({ engineKey }: Readonly<{ engineKey: string }>) {
  const [, setSearchParams] = useSearchParams();
  const { rows } = useRepoConversations(engineKey);
  return createElement(
    "div",
    null,
    createElement(
      "button",
      {
        type: "button",
        "data-testid": "nav-session",
        onClick: () => setSearchParams({ s: "session-2" }),
      },
      "open another session",
    ),
    createElement(
      "button",
      {
        type: "button",
        "data-testid": "nav-rail",
        onClick: () => setSearchParams({ rail: "collapsed" }),
      },
      "collapse rail",
    ),
    createElement("span", { "data-testid": "rows" }, rows === null ? "null" : String(rows.length)),
  );
}

function renderHarness(engineKey = "acme/widget") {
  return render(
    createElement(
      MemoryRouter,
      { initialEntries: ["/repos/acme/widget"] },
      createElement(
        Routes,
        null,
        createElement(Route, {
          path: "/repos/:owner/:repo",
          element: createElement(Harness, { engineKey }),
        }),
      ),
    ),
  );
}

function wrapper({ children }: Readonly<{ children: ReactNode }>) {
  return createElement(
    MemoryRouter,
    { initialEntries: ["/repos/acme/widget"] },
    createElement(
      Routes,
      null,
      createElement(Route, {
        path: "/repos/:owner/:repo",
        element: createElement(Fragment, null, children),
      }),
    ),
  );
}

describe("useRepoConversations", () => {
  it("requests both families scoped by engine_key and merges them newest-first", async () => {
    const capturedEngineKeys: (string | null)[] = [];
    server.use(
      http.get("/api/build-chat/sessions", ({ request }) => {
        capturedEngineKeys.push(new URL(request.url).searchParams.get("engine_key"));
        return HttpResponse.json([
          {
            session_id: "11111111-1111-1111-1111-111111111111",
            created_at: "2026-05-20T09:00:00Z",
            last_message_preview: "build message",
            in_flight: false,
            summary_ready: true,
            engine_key: "acme/widget",
          },
        ]);
      }),
      http.get("/api/explore-chat/sessions", ({ request }) => {
        capturedEngineKeys.push(new URL(request.url).searchParams.get("engine_key"));
        return HttpResponse.json([
          {
            session_id: "22222222-2222-2222-2222-222222222222",
            created_at: "2026-05-20T08:00:00Z",
            last_message_preview: "explore message",
            in_flight: false,
            summary_ready: false,
            consultant_key: "research",
            engine_key: "acme/widget",
          },
        ]);
      }),
    );
    const { result } = renderHook(() => useRepoConversations("acme/widget"), { wrapper });
    await waitFor(() => expect(result.current.rows).not.toBeNull());
    expect(capturedEngineKeys).toEqual(["acme/widget", "acme/widget"]);
    expect(result.current.rows?.map((r) => r.sessionId)).toEqual([
      "11111111-1111-1111-1111-111111111111",
      "22222222-2222-2222-2222-222222222222",
    ]);
    expect(result.current.loadError).toBeNull();
  });

  it("surfaces an error only when both families fail", async () => {
    server.use(
      http.get("/api/build-chat/sessions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.get("/api/explore-chat/sessions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useRepoConversations("acme/widget"), { wrapper });
    await waitFor(() => expect(result.current.loadError).not.toBeNull());
    expect(result.current.rows).toBeNull();
  });

  it("still renders rows when only one family fails", async () => {
    server.use(
      http.get("/api/build-chat/sessions", () =>
        HttpResponse.json([
          {
            session_id: "33333333-3333-3333-3333-333333333333",
            created_at: "2026-05-20T09:00:00Z",
            last_message_preview: "build message",
            in_flight: false,
            summary_ready: false,
            engine_key: "acme/widget",
          },
        ]),
      ),
      http.get("/api/explore-chat/sessions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useRepoConversations("acme/widget"), { wrapper });
    await waitFor(() => expect(result.current.rows).not.toBeNull());
    expect(result.current.rows).toHaveLength(1);
    expect(result.current.loadError).toBeNull();
  });

  it("removeRow is a no-op when called before any rows have loaded", () => {
    server.use(
      http.get("/api/build-chat/sessions", () => new Promise(() => undefined)),
      http.get("/api/explore-chat/sessions", () => new Promise(() => undefined)),
    );
    const { result } = renderHook(() => useRepoConversations("acme/widget"), { wrapper });
    expect(result.current.rows).toBeNull();
    result.current.removeRow("11111111-1111-1111-1111-111111111111");
    expect(result.current.rows).toBeNull();
  });

  it("removeRow filters the given session id out of the current rows", async () => {
    server.use(
      http.get("/api/build-chat/sessions", () =>
        HttpResponse.json([
          {
            session_id: "44444444-4444-4444-4444-444444444444",
            created_at: "2026-05-20T09:00:00Z",
            last_message_preview: "build message",
            in_flight: false,
            summary_ready: false,
            engine_key: "acme/widget",
          },
        ]),
      ),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    const { result } = renderHook(() => useRepoConversations("acme/widget"), { wrapper });
    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    result.current.removeRow("44444444-4444-4444-4444-444444444444");
    await waitFor(() => expect(result.current.rows).toHaveLength(0));
  });

  it("ignores results after the engine_key changes mid-flight", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/build-chat/sessions", ({ request }) => {
        const key = new URL(request.url).searchParams.get("engine_key");
        if (key === "acme/widget") {
          return new Promise((resolve) => {
            gate.resolve = () => resolve(HttpResponse.json([]));
          });
        }
        return HttpResponse.json([]);
      }),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    const { result, rerender } = renderHook(
      ({ engineKey }: { engineKey: string }) => useRepoConversations(engineKey),
      { wrapper, initialProps: { engineKey: "acme/widget" } },
    );
    rerender({ engineKey: "acme/other" });
    await waitFor(() => expect(result.current.rows).not.toBeNull());
    gate.resolve?.();
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current.rows).not.toBeNull();
  });

  it("does not blank the rows while switching to a different open session", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    let callCount = 0;
    server.use(
      http.get("/api/build-chat/sessions", () => {
        callCount += 1;
        if (callCount === 1) {
          return HttpResponse.json([
            {
              session_id: "55555555-5555-5555-5555-555555555555",
              created_at: "2026-05-20T09:00:00Z",
              last_message_preview: "build message",
              in_flight: false,
              summary_ready: false,
              engine_key: "acme/widget",
            },
          ]);
        }
        return new Promise((resolve) => {
          gate.resolve = () => resolve(HttpResponse.json([]));
        });
      }),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    const user = userEvent.setup();
    renderHarness();
    await waitFor(() => expect(screen.getByTestId("rows")).toHaveTextContent("1"));

    await user.click(screen.getByTestId("nav-session"));
    expect(screen.getByTestId("rows")).toHaveTextContent("1");

    gate.resolve?.();
    await waitFor(() => expect(screen.getByTestId("rows")).toHaveTextContent("0"));
  });

  it("does not refetch when an unrelated search param (rail collapse) changes", async () => {
    let callCount = 0;
    server.use(
      http.get("/api/build-chat/sessions", () => {
        callCount += 1;
        return HttpResponse.json([]);
      }),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    const user = userEvent.setup();
    renderHarness();
    await waitFor(() => expect(screen.getByTestId("rows")).toHaveTextContent("0"));
    expect(callCount).toBe(1);

    await user.click(screen.getByTestId("nav-rail"));
    await new Promise((r) => setTimeout(r, 10));
    expect(callCount).toBe(1);
  });

  it("polls for updates on the fallback interval while the tab is visible", async () => {
    let callCount = 0;
    server.use(
      http.get("/api/build-chat/sessions", () => {
        callCount += 1;
        return HttpResponse.json([]);
      }),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    poll.ms = 25;
    renderHook(() => useRepoConversations("acme/widget"), { wrapper });
    await waitFor(() => expect(callCount).toBeGreaterThanOrEqual(3));
  });

  it("pauses the poll while the tab is hidden and resumes it when visible again", async () => {
    let callCount = 0;
    server.use(
      http.get("/api/build-chat/sessions", () => {
        callCount += 1;
        return HttpResponse.json([]);
      }),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    poll.ms = 25;
    renderHook(() => useRepoConversations("acme/widget"), { wrapper });
    await waitFor(() => expect(callCount).toBeGreaterThanOrEqual(2));

    setVisibility("hidden");
    await sleep(40);
    const pausedAt = callCount;
    await sleep(120);
    expect(callCount).toBe(pausedAt);

    setVisibility("visible");
    await waitFor(() => expect(callCount).toBeGreaterThan(pausedAt));
  });

  it("shows loading on an engine switch instead of the previous engine's rows", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/build-chat/sessions", ({ request }) => {
        if (new URL(request.url).searchParams.get("engine_key") === "acme/widget") {
          return HttpResponse.json([
            {
              session_id: "66666666-6666-6666-6666-666666666666",
              created_at: "2026-05-20T09:00:00Z",
              last_message_preview: "widget message",
              in_flight: false,
              summary_ready: false,
              engine_key: "acme/widget",
            },
          ]);
        }
        return new Promise((resolve) => {
          gate.resolve = () => resolve(HttpResponse.json([]));
        });
      }),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    const { result, rerender } = renderHook(
      ({ engineKey }: { engineKey: string }) => useRepoConversations(engineKey),
      { wrapper, initialProps: { engineKey: "acme/widget" } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(1));

    rerender({ engineKey: "acme/other" });
    expect(result.current.rows).toBeNull();
    expect(result.current.loadError).toBeNull();

    await waitFor(() => expect(gate.resolve).not.toBeNull());
    gate.resolve?.();
    await waitFor(() => expect(result.current.rows).toEqual([]));
  });

  it("refetches once when the active session changes and not on the first render", async () => {
    let callCount = 0;
    server.use(
      http.get("/api/build-chat/sessions", () => {
        callCount += 1;
        return HttpResponse.json([]);
      }),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    const user = userEvent.setup();
    renderHarness();
    await waitFor(() => expect(screen.getByTestId("rows")).toHaveTextContent("0"));
    expect(callCount).toBe(1);

    await user.click(screen.getByTestId("nav-session"));
    await waitFor(() => expect(callCount).toBe(2));
    await sleep(80);
    expect(callCount).toBe(2);
  });
});
