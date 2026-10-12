import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../test/server";
import { DraftRowItem, IN_PROGRESS_BADGE_CLASS, toRows, useDraftRows } from "./ChatDraftRows";

const UUID_B = "22222222-2222-2222-2222-222222222222";
const UUID_E = "33333333-3333-3333-3333-333333333333";

function renderRow(
  row: ReturnType<typeof toRows>[number],
  onDeleted: (id: string) => void = () => undefined,
  showEngineMarker = true,
) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route
          path="/"
          element={
            <ul>
              <DraftRowItem row={row} onDeleted={onDeleted} showEngineMarker={showEngineMarker} />
            </ul>
          }
        />
        <Route path="/build" element={<div data-testid="build-page">build</div>} />
        <Route path="/explore/:consultant" element={<div data-testid="explore-page">explore</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const BUILD_SESSION = {
  session_id: UUID_B,
  created_at: "2026-05-20T09:00:00Z",
  last_message_preview: "build message",
  in_flight: false,
  summary_ready: true,
  engine_key: null,
};
const EXPLORE_SESSION = {
  session_id: UUID_E,
  created_at: "2026-05-20T08:00:00Z",
  last_message_preview: "",
  in_flight: true,
  summary_ready: false,
  consultant_key: "brainstorming",
  engine_key: null,
};

describe("toRows", () => {
  it("merges both families and sorts newest-first", () => {
    const rows = toRows([BUILD_SESSION], [EXPLORE_SESSION]);
    expect(rows.map((r) => r.sessionId)).toEqual([UUID_B, UUID_E]);
  });

  it("does not cap the merged result", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({
      session_id: `aaaaaaaa-aaaa-aaaa-aaaa-${String(i).padStart(12, "0")}`,
      created_at: `2026-05-${String(20 - i).padStart(2, "0")}T08:00:00Z`,
      last_message_preview: `msg ${i}`,
      in_flight: false,
      summary_ready: false,
      engine_key: null,
    }));
    expect(toRows(many, [])).toHaveLength(12);
  });

  it("carries the raw consultant_key as fineType", () => {
    const [row] = toRows([], [EXPLORE_SESSION]);
    expect(row?.fineType).toBe("brainstorming");
  });

  it("carries an unknown consultant_key as fineType too, unresolved at this layer", () => {
    const [row] = toRows([], [{ ...EXPLORE_SESSION, consultant_key: "mystery_key" }]);
    expect(row?.fineType).toBe("mystery_key");
  });
});

describe("DraftRowItem", () => {
  it("renders a build row linking to /build with the summary-ready badge", () => {
    const [row] = toRows([BUILD_SESSION], []);
    renderRow(row!);
    expect(screen.getByTestId(`resume-draft-${UUID_B}`)).toHaveAttribute(
      "href",
      `/build?s=${UUID_B}`,
    );
    expect(screen.getByTestId(`fine-type-${UUID_B}`)).toHaveTextContent("Build");
    expect(screen.getByTestId(`status-badge-${UUID_B}`)).toHaveTextContent("Summary ready");
  });

  it("renders no status badge when neither in-flight nor summary-ready", () => {
    const [row] = toRows(
      [{ ...BUILD_SESSION, in_flight: false, summary_ready: false }],
      [],
    );
    renderRow(row!);
    expect(screen.queryByTestId(`status-badge-${UUID_B}`)).not.toBeInTheDocument();
  });

  it("renders an explore row linking to /explore/:consultant with the in-progress badge", () => {
    const [row] = toRows([], [EXPLORE_SESSION]);
    renderRow(row!);
    expect(screen.getByTestId(`resume-draft-${UUID_E}`)).toHaveAttribute(
      "href",
      `/explore/brainstorming?s=${UUID_E}`,
    );
    expect(screen.getByTestId(`status-badge-${UUID_E}`)).toHaveTextContent("In progress");
    expect(screen.getByTestId(`status-badge-${UUID_E}`).className).toBe(IN_PROGRESS_BADGE_CLASS);
  });

  it("shows a placeholder when the last message preview is empty", () => {
    const [row] = toRows([], [EXPLORE_SESSION]);
    renderRow(row!);
    expect(screen.getByText("(no messages yet)")).toBeInTheDocument();
  });

  it("shows the raw consultant key until the consultant list loads, then switches to the proper label", async () => {
    let resolveConsultants!: () => void;
    const consultantsPromise = new Promise<Response>((res) => {
      resolveConsultants = () =>
        res(
          HttpResponse.json([
            { key: "brainstorming", label: "Brainstorming", description: "Diverge.", team: "brainstorming" },
          ]),
        );
    });
    server.use(http.get("/api/explore-chat/consultants", () => consultantsPromise));
    const [row] = toRows([], [EXPLORE_SESSION]);
    renderRow(row!);
    expect(screen.getByTestId(`fine-type-${UUID_E}`)).toHaveTextContent("brainstorming");
    resolveConsultants();
    expect(await screen.findByText("Brainstorming")).toBeInTheDocument();
  });

  it("still shows Build for a build row once the consultant list loads", () => {
    const [row] = toRows([BUILD_SESSION], []);
    renderRow(row!);
    expect(screen.getByTestId(`fine-type-${UUID_B}`)).toHaveTextContent("Build");
  });

  it("shows the repo marker for a non-default repo on the metadata line, beside the timestamp", () => {
    const [row] = toRows([{ ...BUILD_SESSION, engine_key: "acme-org/widgets" }], []);
    renderRow(row!);
    const badge = screen.getByTestId(`engine-badge-draft-repo-${UUID_B}`);
    expect(badge).toHaveTextContent("acme-org/widgets");
    const timestamp = screen.getByTestId(`draft-timestamp-${UUID_B}`);
    expect(timestamp.parentElement).toBe(badge.parentElement);
    expect(screen.getByTestId(`fine-type-${UUID_B}`).parentElement).not.toBe(badge.parentElement);
  });

  it("shows the repo marker for alan's own repo too, not silently unlabelled", () => {
    const [row] = toRows([{ ...BUILD_SESSION, engine_key: "DejvMatikar/alan" }], []);
    renderRow(row!);
    expect(screen.getByTestId(`engine-badge-draft-repo-${UUID_B}`)).toHaveTextContent(
      "DejvMatikar/alan",
    );
  });

  it("renders no repo marker when the row has no engine_key", () => {
    const [row] = toRows([BUILD_SESSION], []);
    renderRow(row!);
    expect(screen.queryByTestId(`engine-badge-draft-repo-${UUID_B}`)).not.toBeInTheDocument();
  });

  it("suppresses the repo marker when showEngineMarker is false", () => {
    const [row] = toRows([{ ...BUILD_SESSION, engine_key: "acme-org/widgets" }], []);
    renderRow(row!, undefined, false);
    expect(screen.queryByTestId(`engine-badge-draft-repo-${UUID_B}`)).not.toBeInTheDocument();
  });

  it("meets the 44px minimum tap-target size on the delete control", () => {
    const [row] = toRows([BUILD_SESSION], []);
    renderRow(row!);
    const del = screen.getByTestId(`delete-draft-${UUID_B}`);
    expect(del.className).toContain("min-h-11");
    expect(del.className).toContain("min-w-11");
  });

  it("has an accessible name that says what will be deleted", () => {
    const [row] = toRows([BUILD_SESSION], []);
    renderRow(row!);
    expect(screen.getByRole("button", { name: "Delete Build chat" })).toBeInTheDocument();
  });

  it("clicking the delete control opens a confirmation dialog and calls no API", async () => {
    let deleteCalls = 0;
    server.use(
      http.delete(`/api/build-chat/sessions/${UUID_B}`, () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const [row] = toRows([BUILD_SESSION], []);
    const user = userEvent.setup();
    renderRow(row!);
    await user.click(screen.getByTestId(`delete-draft-${UUID_B}`));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(deleteCalls).toBe(0);
  });

  it("declining the confirmation leaves the row untouched", async () => {
    let deleteCalls = 0;
    server.use(
      http.delete(`/api/build-chat/sessions/${UUID_B}`, () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const [row] = toRows([BUILD_SESSION], []);
    const user = userEvent.setup();
    renderRow(row!);
    await user.click(screen.getByTestId(`delete-draft-${UUID_B}`));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deleteCalls).toBe(0);
    expect(screen.getByTestId(`draft-row-${UUID_B}`)).toBeInTheDocument();
  });

  it("confirming deletes a build row via the build endpoint and notifies onDeleted", async () => {
    const deleted: string[] = [];
    server.use(
      http.delete(`/api/build-chat/sessions/${UUID_B}`, () => {
        deleted.push(UUID_B);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const [row] = toRows([BUILD_SESSION], []);
    const onDeleted = (id: string) => deleted.push(`notified:${id}`);
    const user = userEvent.setup();
    renderRow(row!, onDeleted);
    await user.click(screen.getByTestId(`delete-draft-${UUID_B}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(deleted).toContain(UUID_B));
    await waitFor(() => expect(deleted).toContain(`notified:${UUID_B}`));
  });

  it("confirming deletes an explore row via the explore endpoint", async () => {
    const deleted: string[] = [];
    server.use(
      http.delete(`/api/explore-chat/sessions/${UUID_E}`, () => {
        deleted.push(UUID_E);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const [row] = toRows([], [EXPLORE_SESSION]);
    const user = userEvent.setup();
    renderRow(row!);
    await user.click(screen.getByTestId(`delete-draft-${UUID_E}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(deleted).toContain(UUID_E));
  });

  it("shows an inline alert and keeps the row when delete fails", async () => {
    server.use(
      http.delete(`/api/build-chat/sessions/${UUID_B}`, () =>
        HttpResponse.json({ detail: "forbidden" }, { status: 403 }),
      ),
    );
    const [row] = toRows([BUILD_SESSION], []);
    const user = userEvent.setup();
    renderRow(row!);
    await user.click(screen.getByTestId(`delete-draft-${UUID_B}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("403");
    expect(screen.getByTestId(`draft-row-${UUID_B}`)).toBeInTheDocument();
  });

  it("ignores delete resolution after row unmount", async () => {
    const apiModule = await import("../api");
    const orig = apiModule.api.deleteBuildChatSession;
    const gate: { resolve: (() => void) | null } = { resolve: null };
    apiModule.api.deleteBuildChatSession = () =>
      new Promise<void>((resolve) => {
        gate.resolve = resolve;
      });
    try {
      const [row] = toRows([BUILD_SESSION], []);
      const user = userEvent.setup();
      const { unmount } = renderRow(row!);
      await user.click(screen.getByTestId(`delete-draft-${UUID_B}`));
      await user.click(screen.getByRole("button", { name: "Delete chat" }));
      unmount();
      gate.resolve?.();
      await new Promise((r) => setTimeout(r, 10));
    } finally {
      apiModule.api.deleteBuildChatSession = orig;
    }
  });

  it("ignores delete rejection after row unmount", async () => {
    const apiModule = await import("../api");
    const orig = apiModule.api.deleteBuildChatSession;
    const gate: { reject: ((err: Error) => void) | null } = { reject: null };
    apiModule.api.deleteBuildChatSession = () =>
      new Promise<void>((_resolve, reject) => {
        gate.reject = reject;
      });
    try {
      const [row] = toRows([BUILD_SESSION], []);
      const user = userEvent.setup();
      const { unmount } = renderRow(row!);
      await user.click(screen.getByTestId(`delete-draft-${UUID_B}`));
      await user.click(screen.getByRole("button", { name: "Delete chat" }));
      unmount();
      gate.reject?.(new Error("post-unmount"));
      await new Promise((r) => setTimeout(r, 10));
    } finally {
      apiModule.api.deleteBuildChatSession = orig;
    }
  });
});

describe("useDraftRows", () => {
  function serveSessions(build: () => Response | Promise<Response>, explore: () => Response | Promise<Response>) {
    const projectIds: (string | null)[] = [];
    server.use(
      http.get("/api/build-chat/sessions", ({ request }) => {
        projectIds.push(new URL(request.url).searchParams.get("project_id"));
        return build();
      }),
      http.get("/api/explore-chat/sessions", explore),
    );
    return projectIds;
  }

  it("requests both families for the project and merges them newest-first", async () => {
    const projectIds = serveSessions(
      () => HttpResponse.json([BUILD_SESSION]),
      () => HttpResponse.json([EXPLORE_SESSION]),
    );
    const { result } = renderHook(() => useDraftRows("p1"));
    expect(result.current.rows).toBeNull();
    await waitFor(() => expect(result.current.rows).not.toBeNull());
    expect(projectIds).toEqual(["p1"]);
    expect(result.current.rows?.map((r) => r.sessionId)).toEqual([UUID_B, UUID_E]);
    expect(result.current.loadError).toBeNull();
  });

  it("requests the unfiltered lists when no project is given", async () => {
    const projectIds = serveSessions(
      () => HttpResponse.json([BUILD_SESSION]),
      () => HttpResponse.json([]),
    );
    const { result } = renderHook(() => useDraftRows());
    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    expect(projectIds).toEqual([null]);
  });

  it("shows loading on a project switch instead of the previous project's rows", async () => {
    let releaseSecond: () => void = () => undefined;
    let projectSwitched = false;
    serveSessions(
      () => {
        if (projectSwitched) {
          return new Promise<Response>((resolve) => {
            releaseSecond = () => resolve(HttpResponse.json([]));
          });
        }
        return HttpResponse.json([BUILD_SESSION]);
      },
      () => HttpResponse.json([]),
    );
    const { result, rerender } = renderHook(({ id }: { id: string }) => useDraftRows(id), {
      initialProps: { id: "p1" },
    });
    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    projectSwitched = true;
    rerender({ id: "p2" });
    expect(result.current.rows).toBeNull();
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.rows).toBeNull();
    releaseSecond();
    await waitFor(() => expect(result.current.rows).toEqual([]));
  });

  it("surfaces an error only when both families fail", async () => {
    serveSessions(
      () => HttpResponse.json({ detail: "boom" }, { status: 500 }),
      () => HttpResponse.json({ detail: "boom" }, { status: 500 }),
    );
    const { result } = renderHook(() => useDraftRows("p1"));
    await waitFor(() => expect(result.current.loadError).not.toBeNull());
    expect(result.current.rows).toBeNull();
  });

  it("keeps the build rows when the explore family fails", async () => {
    serveSessions(
      () => HttpResponse.json([BUILD_SESSION]),
      () => HttpResponse.json({ detail: "boom" }, { status: 500 }),
    );
    const { result } = renderHook(() => useDraftRows("p1"));
    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    expect(result.current.rows?.[0]?.family).toBe("build");
    expect(result.current.loadError).toBeNull();
  });

  it("keeps the explore rows when the build family fails", async () => {
    serveSessions(
      () => HttpResponse.json({ detail: "boom" }, { status: 500 }),
      () => HttpResponse.json([EXPLORE_SESSION]),
    );
    const { result } = renderHook(() => useDraftRows("p1"));
    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    expect(result.current.rows?.[0]?.family).toBe("explore");
    expect(result.current.loadError).toBeNull();
  });

  it("removeRow drops the session from the cached rows without another request", async () => {
    const projectIds = serveSessions(
      () => HttpResponse.json([BUILD_SESSION]),
      () => HttpResponse.json([EXPLORE_SESSION]),
    );
    const { result } = renderHook(() => useDraftRows("p1"));
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    await act(async () => {
      result.current.removeRow(UUID_B);
      await Promise.resolve();
    });
    expect(result.current.rows?.map((r) => r.sessionId)).toEqual([UUID_E]);
    expect(projectIds).toEqual(["p1"]);
  });

  it("removeRow is a no-op before the rows have loaded", () => {
    serveSessions(
      () => new Promise<Response>(() => undefined),
      () => new Promise<Response>(() => undefined),
    );
    const { result } = renderHook(() => useDraftRows("p1"));
    act(() => {
      result.current.removeRow(UUID_B);
    });
    expect(result.current.rows).toBeNull();
  });
});
