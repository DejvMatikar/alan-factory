import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { ShellExploreProvider } from "./ShellExploreProvider";
import { server } from "../test/server";
import RepoShell from "./RepoShell";
import { STICKY_SLOT_TOP_CLASS } from "./stickyOffsets";
import { RAIL_COLLAPSE_STORAGE_KEY } from "./useRailCollapse";

function mockEngines(repos: { owner: string; repo: string }[]): void {
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: repos.map(({ owner, repo }) => ({ repo: { owner, repo }, posture: "autonomous" })),
      }),
    ),
    http.get("/api/build-chat/sessions", () => HttpResponse.json([])),
    http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
  );
}

function renderAt(initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <ShellExploreProvider>
        <Routes>
          <Route path="/" element={<div data-testid="redirected-home">home</div>} />
          <Route path="/repos/:owner/:repo" element={<RepoShell />}>
            <Route index element={<div data-testid="dashboard-outlet">dashboard</div>} />
            <Route path="build" element={<div data-testid="build-outlet">build</div>} />
          </Route>
        </Routes>
      </ShellExploreProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  mockEngines([{ owner: "acme", repo: "widget" }]);
});

describe("RepoShell — layout persistence", () => {
  it("renders the rail and the index route's dashboard content, with no shell-level title heading", async () => {
    renderAt("/repos/acme/widget");
    expect(await screen.findByTestId("conversation-rail-acme/widget")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-outlet")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-shell-title")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Widget" })).not.toBeInTheDocument();
  });

  it("swaps only the outlet — rail stays — when navigating to a nested chat route", async () => {
    renderAt("/repos/acme/widget/build");
    expect(await screen.findByTestId("conversation-rail-acme/widget")).toBeInTheDocument();
    expect(screen.getByTestId("build-outlet")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-outlet")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-shell-title")).not.toBeInTheDocument();
  });

  it("locks the rail slot to the viewport at sm and up, beneath the sticky header's height plus main's own padding, with self-start so the flex row doesn't stretch it", async () => {
    renderAt("/repos/acme/widget");
    const slot = await screen.findByTestId("repo-shell-rail-slot");
    expect(slot.className).toContain("sm:sticky");
    expect(slot.className).toContain(STICKY_SLOT_TOP_CLASS);
    expect(slot.className).not.toContain("sm:top-16");
    expect(slot.className).toContain("sm:self-start");
  });

  it("renders nothing when mounted off a route carrying no owner/repo params", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ShellExploreProvider>
          <Routes>
            <Route path="/" element={<RepoShell />} />
          </Routes>
        </ShellExploreProvider>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("repo-page")).not.toBeInTheDocument();
  });
});

describe("RepoShell — collapse", () => {
  it("collapses the rail behind a compact indicator that reopens it", async () => {
    const user = userEvent.setup();
    renderAt("/repos/acme/widget");
    await screen.findByTestId("conversation-rail-acme/widget");
    await user.click(screen.getByTestId("rail-collapse"));
    expect(screen.queryByTestId("conversation-rail-acme/widget")).not.toBeInTheDocument();
    const indicator = screen.getByTestId("rail-collapsed-acme/widget");
    expect(indicator).toBeInTheDocument();
    expect(sessionStorage.getItem(RAIL_COLLAPSE_STORAGE_KEY)).toBe("collapsed");

    await user.click(screen.getByTestId("rail-expand"));
    expect(await screen.findByTestId("conversation-rail-acme/widget")).toBeInTheDocument();
  });

  it("the collapsed indicator still exposes a compact way to start a build conversation", async () => {
    const user = userEvent.setup();
    renderAt("/repos/acme/widget");
    await screen.findByTestId("conversation-rail-acme/widget");
    await user.click(screen.getByTestId("rail-collapse"));
    await user.click(screen.getByTestId("rail-collapsed-start-build"));
    expect(await screen.findByTestId("build-outlet")).toBeInTheDocument();
  });

  it("persists a collapsed rail across navigation to a nested route", async () => {
    const user = userEvent.setup();
    renderAt("/repos/acme/widget");
    await screen.findByTestId("conversation-rail-acme/widget");
    await user.click(screen.getByTestId("rail-collapse"));
    expect(screen.getByTestId("rail-collapsed-acme/widget")).toBeInTheDocument();

    await user.click(screen.getByTestId("rail-collapsed-start-build"));
    await screen.findByTestId("build-outlet");
    expect(screen.getByTestId("rail-collapsed-acme/widget")).toBeInTheDocument();
    expect(screen.queryByTestId("conversation-rail-acme/widget")).not.toBeInTheDocument();
  });
});

describe("RepoShell — accent frame", () => {
  it("paints the accent as two parallel lines — the rail slot's outer (left) edge and the content column's outer (right) edge", async () => {
    renderAt("/repos/acme/widget");
    const railSlot = await screen.findByTestId("repo-shell-rail-slot");
    const contentSlot = screen.getByTestId("repo-shell-content-slot");
    expect(railSlot.className).toContain("border-l-2");
    expect(railSlot.className).toContain("border-[var(--accent-edge)]");
    expect(contentSlot.className).toContain("border-r-2");
    expect(contentSlot.className).toContain("border-[var(--accent-edge)]");
    await waitFor(() => {
      expect(railSlot.style.getPropertyValue("--accent-edge")).toMatch(/^#[0-9a-f]{6}$/i);
    });
    expect(contentSlot.style.getPropertyValue("--accent-edge")).toBe(
      railSlot.style.getPropertyValue("--accent-edge"),
    );
  });

  it("keeps a thin accent line on the rail slot when the rail is collapsed", async () => {
    const user = userEvent.setup();
    renderAt("/repos/acme/widget");
    await screen.findByTestId("conversation-rail-acme/widget");
    await user.click(screen.getByTestId("rail-collapse"));
    const railSlot = screen.getByTestId("repo-shell-rail-slot");
    expect(screen.getByTestId("rail-collapsed-acme/widget")).toBeInTheDocument();
    expect(railSlot.className).toContain("border-l-2");
    expect(railSlot.className).toContain("border-[var(--accent-edge)]");
  });

  it("spaces both frame lines off their content and glows both edges for a resolved accent", async () => {
    renderAt("/repos/acme/widget");
    const railSlot = await screen.findByTestId("repo-shell-rail-slot");
    const contentSlot = screen.getByTestId("repo-shell-content-slot");
    expect(railSlot.className).toContain("pl-4");
    expect(contentSlot.className).toContain("pr-4");
    await waitFor(() => {
      expect(railSlot.className).toContain("shadow-[-6px_0_18px_var(--accent-glow)]");
    });
    expect(contentSlot.className).toContain("shadow-[6px_0_18px_var(--accent-glow)]");
    expect(railSlot.style.getPropertyValue("--accent-glow")).toMatch(/^#[0-9a-f]{8}$/i);
  });

  it("carries top, left, and right pad on the rail slot, and top, left, right, and bottom pad on the content slot", async () => {
    renderAt("/repos/acme/widget");
    const railSlot = await screen.findByTestId("repo-shell-rail-slot");
    const contentSlot = screen.getByTestId("repo-shell-content-slot");
    expect(railSlot.className).toContain("pt-4");
    expect(railSlot.className).toContain("pl-4");
    expect(railSlot.className).toContain("pr-4");
    expect(contentSlot.className).toContain("pt-4");
    expect(contentSlot.className).toContain("pl-4");
    expect(contentSlot.className).toContain("pr-4");
    expect(contentSlot.className).toContain("pb-4");
  });

  it("insets the rail from its column's right edge by the same amount as its left edge", async () => {
    renderAt("/repos/acme/widget");
    const railSlot = await screen.findByTestId("repo-shell-rail-slot");
    const leftPad = /(?:^|\s)pl-(\d+)/.exec(railSlot.className)?.[1];
    const rightPad = /(?:^|\s)pr-(\d+)/.exec(railSlot.className)?.[1];
    expect(leftPad).toBeDefined();
    expect(rightPad).toBe(leftPad);
  });

  it("keeps the top, left, and right pad on the rail slot when the rail is collapsed", async () => {
    const user = userEvent.setup();
    renderAt("/repos/acme/widget");
    await screen.findByTestId("conversation-rail-acme/widget");
    await user.click(screen.getByTestId("rail-collapse"));
    const railSlot = screen.getByTestId("repo-shell-rail-slot");
    expect(screen.getByTestId("rail-collapsed-acme/widget")).toBeInTheDocument();
    expect(railSlot.className).toContain("pt-4");
    expect(railSlot.className).toContain("pl-4");
    expect(railSlot.className).toContain("pr-4");
  });

  it("carries the content-column line for a nested chat route too, not just the dashboard index route", async () => {
    renderAt("/repos/acme/widget/build");
    const contentSlot = await screen.findByTestId("repo-shell-content-slot");
    expect(contentSlot.className).toContain("border-r-2");
    expect(screen.getByTestId("build-outlet")).toBeInTheDocument();
  });

  it("swaps both edges to the neutral border token when the repository has explicitly chosen no accent", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            {
              repo: { owner: "acme", repo: "widget" },
              posture: "autonomous",
              accent: { color: null, intensity: "standard" },
            },
          ],
        }),
      ),
    );
    renderAt("/repos/acme/widget");
    const railSlot = await screen.findByTestId("repo-shell-rail-slot");
    const contentSlot = screen.getByTestId("repo-shell-content-slot");
    await waitFor(() => {
      expect(railSlot.className).toContain("border-border");
    });
    expect(railSlot.className).not.toContain("border-[var(--accent-edge)]");
    expect(contentSlot.className).toContain("border-border");
    expect(contentSlot.className).not.toContain("border-[var(--accent-edge)]");
    expect(railSlot.className).not.toContain("shadow-[-6px_0_18px_var(--accent-glow)]");
    expect(contentSlot.className).not.toContain("shadow-[6px_0_18px_var(--accent-glow)]");
  });

  it("resolves a direct-linked hidden repository's own accent instead of falling back to the neutral token", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            {
              repo: { owner: "acme", repo: "widget" },
              posture: "autonomous",
              hidden: true,
              accent: { color: "ruby", intensity: "standard" },
            },
          ],
        }),
      ),
    );
    renderAt("/repos/acme/widget");
    const railSlot = await screen.findByTestId("repo-shell-rail-slot");
    await waitFor(() => {
      expect(railSlot.style.getPropertyValue("--accent-edge")).toMatch(/^#[0-9a-f]{6}$/i);
    });
    expect(railSlot.className).toContain("border-[var(--accent-edge)]");
    expect(railSlot.className).not.toContain("border-border");
  });
});

describe("RepoShell — mobile drawer trigger", () => {
  it("opens the drawer from the compact repository trigger and closes it again", async () => {
    const user = userEvent.setup();
    renderAt("/repos/acme/widget");
    await screen.findByTestId("repo-drawer-trigger");
    expect(screen.queryByTestId("repo-drawer")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("repo-drawer-trigger"));
    expect(await screen.findByTestId("repo-drawer")).toBeInTheDocument();
    await user.click(screen.getByTestId("repo-drawer-close"));
    expect(screen.queryByTestId("repo-drawer")).not.toBeInTheDocument();
  });
});
