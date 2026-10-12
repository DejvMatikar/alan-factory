import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { ResolvedAccent } from "../lib/repoAccent";
import { ShellExploreProvider, useShellExplore } from "./ShellExploreProvider";
import { server } from "../test/server";
import ConversationRail from "./ConversationRail";
import { RAIL_MAX_H_CLASS } from "./stickyOffsets";

function ExploreProbe() {
  const { target } = useShellExplore();
  return <div data-testid="explore-probe">{target?.repoKey ?? "none"}</div>;
}

function renderRail(
  initial = "/repos/acme/widget",
  onCollapse?: () => void,
  resolvedAccent: ResolvedAccent | null = null,
) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <ShellExploreProvider>
        <ExploreProbe />
        <Routes>
          <Route
            path="/repos/:owner/:repo"
            element={
              <ConversationRail
                engineKey="acme/widget"
                resolvedAccent={resolvedAccent}
                {...(onCollapse ? { onCollapse } : {})}
              />
            }
          />
        </Routes>
      </ShellExploreProvider>
    </MemoryRouter>,
  );
}

const BUILD_SESSION = {
  session_id: "11111111-1111-1111-1111-111111111111",
  created_at: "2026-05-20T09:00:00Z",
  last_message_preview: "build message",
  in_flight: false,
  summary_ready: true,
  engine_key: "acme/widget",
};

const EXPLORE_SESSION = {
  session_id: "22222222-2222-2222-2222-222222222222",
  created_at: "2026-05-20T08:00:00Z",
  last_message_preview: "explore message",
  in_flight: true,
  summary_ready: false,
  consultant_key: "brainstorming",
  engine_key: "acme/widget",
};

function seedSessions(builds: unknown[] = [], explores: unknown[] = []) {
  server.use(
    http.get("/api/build-chat/sessions", () => HttpResponse.json(builds)),
    http.get("/api/explore-chat/sessions", () => HttpResponse.json(explores)),
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: [{ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" }],
      }),
    ),
  );
}

describe("ConversationRail — empty and error states", () => {
  it("shows the one-line empty state in the app's existing sentence shape", async () => {
    seedSessions();
    renderRail();
    expect(await screen.findByTestId("conversation-rail-empty")).toHaveTextContent(
      "No conversations yet.",
    );
  });

  it("shows an error banner when both families fail", async () => {
    server.use(
      http.get("/api/build-chat/sessions", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderRail();
    const alert = await screen.findByTestId("conversation-rail-error");
    expect(alert).toHaveTextContent("500");
  });
});

describe("ConversationRail — list", () => {
  it("caps the whole rail — not just the scroll region — to the viewport, so the collapse control stays reachable", async () => {
    seedSessions([BUILD_SESSION], [EXPLORE_SESSION]);
    renderRail();
    await screen.findByTestId(`rail-row-${BUILD_SESSION.session_id}`);
    const rail = screen.getByTestId("conversation-rail-acme/widget");
    expect(rail.className).toContain(RAIL_MAX_H_CLASS);
    const scroll = screen.getByTestId("conversation-rail-scroll");
    expect(scroll.className).toContain("overflow-y-auto");
    expect(scroll.className).not.toMatch(/max-h-\[/);
    const rows = screen.getAllByTestId(/^rail-row-[0-9a-f]{8}-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      `rail-row-${BUILD_SESSION.session_id}`,
      `rail-row-${EXPLORE_SESSION.session_id}`,
    ]);
  });

  it("renders no box chrome around the scroll region — no rounded corners, no border", async () => {
    seedSessions([BUILD_SESSION], []);
    renderRail();
    await screen.findByTestId(`rail-row-${BUILD_SESSION.session_id}`);
    const scroll = screen.getByTestId("conversation-rail-scroll");
    expect(scroll.className).not.toContain("rounded-md");
    expect(scroll.className).not.toContain("border");
  });

  it("carries no compensating left inset on the scroll container, since rows no longer consume layout width for their selection accent", async () => {
    seedSessions([BUILD_SESSION], []);
    renderRail();
    await screen.findByTestId(`rail-row-${BUILD_SESSION.session_id}`);
    const scroll = screen.getByTestId("conversation-rail-scroll");
    expect(scroll.className).not.toContain("-ml-1");
  });

  it("marks the row whose session id matches ?s= as active", async () => {
    seedSessions([BUILD_SESSION], [EXPLORE_SESSION]);
    renderRail(`/repos/acme/widget?s=${EXPLORE_SESSION.session_id}`);
    await screen.findByTestId(`rail-row-${EXPLORE_SESSION.session_id}`);
    expect(
      screen.getByTestId(`rail-row-link-${EXPLORE_SESSION.session_id}`),
    ).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByTestId(`rail-row-link-${BUILD_SESSION.session_id}`),
    ).not.toHaveAttribute("aria-current");
  });
});

describe("ConversationRail — height budget", () => {
  it("subtracts the sticky slot offset (6rem) plus one frame pad unit (pt-4, 1rem) from the viewport height", async () => {
    seedSessions([BUILD_SESSION], []);
    renderRail();
    await screen.findByTestId(`rail-row-${BUILD_SESSION.session_id}`);
    const rail = screen.getByTestId("conversation-rail-acme/widget");
    expect(rail.className).toContain("max-h-[calc(100dvh-7rem)]");
    expect(rail.className).toContain(RAIL_MAX_H_CLASS);
  });
});

describe("ConversationRail — start controls", () => {
  it("renders the Build and Explore controls beneath the Conversations label, each carrying its shortcut in its accessible name", () => {
    seedSessions();
    renderRail();
    const label = screen.getByTestId("rail-heading");
    const controls = screen.getByTestId("rail-start-controls-acme/widget");
    expect(label.compareDocumentPosition(controls) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("rail-start-build-acme/widget")).toHaveAccessibleName(/shortcut Control Shift X/);
    expect(screen.getByTestId("rail-start-explore-acme/widget")).toHaveAccessibleName(/shortcut Control Shift E/);
  });

  it("supplies the repository's own accent as a CSS variable for descendants (the row selection edge), without painting a visible edge of its own — that now lives on the shell frame", async () => {
    seedSessions();
    renderRail("/repos/acme/widget", undefined, { color: "iris", intensity: "standard" });
    const rail = await screen.findByTestId("conversation-rail-acme/widget");
    expect(rail.className).not.toContain("border-l-2");
    expect(rail.className).not.toContain("pl-3");
    expect(rail.style.getPropertyValue("--accent-edge")).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("gives Build and Explore a plain-language description that states purpose only, not the shortcut", () => {
    seedSessions();
    renderRail();
    expect(
      screen.getByText("Start a new build conversation to scope and dispatch work in this repository."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Open a free-form conversation to research or brainstorm about this repository."),
    ).toBeInTheDocument();
  });

  it("wires Build and Explore's descriptions via aria-describedby for assistive technology", () => {
    seedSessions();
    renderRail();
    const build = screen.getByTestId("rail-start-build-acme/widget");
    const buildDescribedBy = build.getAttribute("aria-describedby");
    expect(buildDescribedBy).not.toBeNull();
    expect(document.getElementById(buildDescribedBy ?? "")).toHaveTextContent(
      "Start a new build conversation to scope and dispatch work in this repository.",
    );
    const explore = screen.getByTestId("rail-start-explore-acme/widget");
    const exploreDescribedBy = explore.getAttribute("aria-describedby");
    expect(exploreDescribedBy).not.toBeNull();
    expect(document.getElementById(exploreDescribedBy ?? "")).toHaveTextContent(
      "Open a free-form conversation to research or brainstorm about this repository.",
    );
  });

  it("renders no collapse control when onCollapse is not provided", () => {
    seedSessions();
    renderRail();
    expect(screen.queryByTestId("rail-collapse")).not.toBeInTheDocument();
  });

  it("renders an expanded collapse control below the scroll region, after the label and start controls, and calls it on click", async () => {
    seedSessions();
    const onCollapse = vi.fn();
    const user = userEvent.setup();
    renderRail("/repos/acme/widget", onCollapse);
    const label = screen.getByTestId("rail-heading");
    const scroll = screen.getByTestId("conversation-rail-scroll");
    const collapseButton = screen.getByTestId("rail-collapse");
    expect(collapseButton).toHaveAttribute("aria-expanded", "true");
    expect(label.compareDocumentPosition(collapseButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(scroll.compareDocumentPosition(collapseButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(collapseButton);
    expect(onCollapse).toHaveBeenCalledOnce();
  });

  it("Build navigates to the scoped build route", async () => {
    seedSessions();
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/repos/acme/widget"]}>
        <ShellExploreProvider>
          <Routes>
            <Route
              path="/repos/:owner/:repo"
              element={<ConversationRail engineKey="acme/widget" resolvedAccent={null} />}
            />
            <Route path="/repos/:owner/:repo/build" element={<div data-testid="build-page">build</div>} />
          </Routes>
        </ShellExploreProvider>
      </MemoryRouter>,
    );
    await user.click(await screen.findByTestId("rail-start-build-acme/widget"));
    expect(await screen.findByTestId("build-page")).toBeInTheDocument();
  });

  it("Explore opens the shared explore target scoped to this repository", async () => {
    seedSessions();
    const user = userEvent.setup();
    renderRail();
    await user.click(await screen.findByTestId("rail-start-explore-acme/widget"));
    expect(screen.getByTestId("explore-probe")).toHaveTextContent("acme/widget");
  });
});

describe("ConversationRail — delete outcomes", () => {
  it("deleting a non-active row leaves the rail's route untouched", async () => {
    seedSessions([BUILD_SESSION], []);
    server.use(
      http.delete(`/api/build-chat/sessions/${BUILD_SESSION.session_id}`, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    const user = userEvent.setup();
    renderRail("/repos/acme/widget");
    await screen.findByTestId(`rail-row-${BUILD_SESSION.session_id}`);
    await user.click(screen.getByTestId(`rail-row-delete-${BUILD_SESSION.session_id}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() =>
      expect(screen.queryByTestId(`rail-row-${BUILD_SESSION.session_id}`)).not.toBeInTheDocument(),
    );
    expect(screen.queryByTestId("conversation-rail-announcement")).toHaveTextContent("");
  });

  it("deleting the active row announces the outcome and returns to the repository dashboard", async () => {
    seedSessions([BUILD_SESSION], []);
    server.use(
      http.delete(`/api/build-chat/sessions/${BUILD_SESSION.session_id}`, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/repos/acme/widget?s=${BUILD_SESSION.session_id}`]}>
        <ShellExploreProvider>
          <Routes>
            <Route
              path="/repos/:owner/:repo"
              element={<ConversationRail engineKey="acme/widget" resolvedAccent={null} />}
            />
          </Routes>
        </ShellExploreProvider>
      </MemoryRouter>,
    );
    await screen.findByTestId(`rail-row-${BUILD_SESSION.session_id}`);
    await user.click(screen.getByTestId(`rail-row-delete-${BUILD_SESSION.session_id}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() =>
      expect(screen.getByTestId("conversation-rail-announcement")).toHaveTextContent(
        "Conversation deleted",
      ),
    );
  });
});
