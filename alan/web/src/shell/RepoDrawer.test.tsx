import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { useEngines } from "../hooks/useEngines";
import { accentEdgeHex } from "../lib/repoAccent";
import { ShellExploreProvider } from "./ShellExploreProvider";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeConfiguredEngineSummaryFor,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
  type DefaultKeyState,
} from "../test/engineFixtures";
import { server } from "../test/server";
import RepoDrawer from "./RepoDrawer";

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

function containerOf(dialog: HTMLElement): HTMLElement {
  const el = dialog.querySelector("div");
  if (el === null) throw new Error("drawer container div not found");
  return el;
}

function LauncherAndDrawer() {
  const [open, setOpen] = useState(false);
  return (
    <MemoryRouter initialEntries={["/repos/acme/widget"]}>
      <ShellExploreProvider>
        <button type="button" data-testid="launcher" onClick={() => setOpen(true)}>
          Open
        </button>
        <Routes>
          <Route
            path="/repos/:owner/:repo"
            element={open ? <RepoDrawer engineKey="acme/widget" onClose={() => setOpen(false)} /> : null}
          />
        </Routes>
      </ShellExploreProvider>
    </MemoryRouter>
  );
}

describe("RepoDrawer", () => {
  it("renders the repository switcher list above the conversations", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const user = userEvent.setup();
    render(<LauncherAndDrawer />);
    await user.click(screen.getByTestId("launcher"));
    const switcher = await screen.findByTestId("repo-drawer-switcher");
    const rail = screen.getByTestId("conversation-rail-acme/widget");
    expect(switcher.compareDocumentPosition(rail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("repo-drawer-repo-acme/widget")).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("repo-drawer-repo-DejvMatikar/alan")).not.toHaveAttribute("aria-current");
  });

  it("colours the selected entry's name in its own accent, leaving the unselected entry muted", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const user = userEvent.setup();
    render(<LauncherAndDrawer />);
    await user.click(screen.getByTestId("launcher"));
    const selected = await screen.findByTestId("repo-drawer-repo-acme/widget");
    const unselected = screen.getByTestId("repo-drawer-repo-DejvMatikar/alan");
    expect(selected.className).toContain("text-[var(--accent-fg)]");
    expect(selected.style.getPropertyValue("--accent-edge")).toMatch(/^#[0-9a-f]{6}$/i);
    expect(unselected.className).toContain("text-text-muted");
    expect(unselected.className).not.toContain("text-[var(--accent-fg)]");
  });

  it("renders the neutral border/text tokens for the selected entry when the repository has explicitly chosen no accent", async () => {
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
      http.get("/api/build-chat/sessions", () => HttpResponse.json([])),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    const user = userEvent.setup();
    render(<LauncherAndDrawer />);
    await user.click(screen.getByTestId("launcher"));
    const selected = await screen.findByTestId("repo-drawer-repo-acme/widget");
    expect(selected.className).toContain("border-border");
    expect(selected.className).toContain("text-text-primary");
    expect(selected.className).not.toContain("border-[var(--accent-edge)]");
    expect(selected.className).not.toContain("text-[var(--accent-fg)]");
  });

  it("bounds the panel to the dialog's own height so only the rail scrolls internally", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    const user = userEvent.setup();
    render(<LauncherAndDrawer />);
    await user.click(screen.getByTestId("launcher"));
    const dialog = await screen.findByTestId("repo-drawer");
    const panel = containerOf(dialog);
    expect(panel.className).toContain("h-full");
    expect(panel.className).not.toContain("overflow-y-auto");
    expect(screen.getByTestId("conversation-rail-acme/widget").className).toMatch(/max-h-\[/);
    const scroll = screen.getByTestId("conversation-rail-scroll");
    expect(scroll.className).toContain("overflow-y-auto");
    expect(scroll.className).not.toMatch(/max-h-\[/);
  });

  it("renders the rail with no collapse control", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    const user = userEvent.setup();
    render(<LauncherAndDrawer />);
    await user.click(screen.getByTestId("launcher"));
    await screen.findByTestId("conversation-rail-acme/widget");
    expect(screen.queryByTestId("rail-collapse")).not.toBeInTheDocument();
  });

  it("resolves its own accent from the unfiltered engine list so a hidden repository reached by direct link keeps its accent, even though the switcher list stays hidden-filtered", async () => {
    const visible: { owner: string; repo: string } = { owner: "acme", repo: "widget" };
    const hidden = {
      repo: { owner: "acme", repo: "shadow" },
      posture: "autonomous",
      hidden: true,
      accent: { color: "plum" as const, intensity: "standard" as const },
    };
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [{ repo: visible, posture: "autonomous" }, hidden],
        }),
      ),
      http.get("/api/build-chat/sessions", () => HttpResponse.json([])),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ShellExploreProvider>
          <RepoDrawer engineKey="acme/shadow" onClose={() => undefined} />
        </ShellExploreProvider>
      </MemoryRouter>,
    );
    const rail = await screen.findByTestId("conversation-rail-acme/shadow");
    expect(rail.style.getPropertyValue("--accent-edge")).toBe(accentEdgeHex("plum", "standard"));
    const switcher = await screen.findByTestId("repo-drawer-switcher");
    expect(within(switcher).queryByTestId("repo-drawer-repo-acme/shadow")).not.toBeInTheDocument();
    expect(within(switcher).getByTestId("repo-drawer-repo-acme/widget")).toBeInTheDocument();
  });

  it("resolves the switcher list's entry colours from the unfiltered engine list, so a hidden repository's explicit colour is honoured as taken the same way the rail's own accent honours it", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            { repo: { owner: "acme", repo: "widget" }, posture: "autonomous" },
            {
              repo: { owner: "acme", repo: "shadow" },
              posture: "autonomous",
              hidden: true,
              accent: { color: "magenta", intensity: "standard" },
            },
          ],
        }),
      ),
      http.get("/api/build-chat/sessions", () => HttpResponse.json([])),
      http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
    );
    const user = userEvent.setup();
    render(<LauncherAndDrawer />);
    await user.click(screen.getByTestId("launcher"));
    const entry = await screen.findByTestId("repo-drawer-repo-acme/widget");
    expect(entry.style.getPropertyValue("--accent-edge")).toBe(accentEdgeHex("teal", "standard"));
  });

  it("renders no repository switcher list when nothing is connected", async () => {
    mockEngines([]);
    const user = userEvent.setup();
    render(<LauncherAndDrawer />);
    await user.click(screen.getByTestId("launcher"));
    await screen.findByTestId("conversation-rail-acme/widget");
    expect(screen.queryByTestId("repo-drawer-switcher")).not.toBeInTheDocument();
  });

  it("closing via the close button calls onClose", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ShellExploreProvider>
          <RepoDrawer engineKey="acme/widget" onClose={onClose} />
        </ShellExploreProvider>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("repo-drawer-close"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Escape (native dialog cancel) calls onClose", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ShellExploreProvider>
          <RepoDrawer engineKey="acme/widget" onClose={onClose} />
        </ShellExploreProvider>
      </MemoryRouter>,
    );
    fireEvent(await screen.findByTestId("repo-drawer"), new Event("cancel"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking the backdrop calls onClose, clicking inside the panel does not", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ShellExploreProvider>
          <RepoDrawer engineKey="acme/widget" onClose={onClose} />
        </ShellExploreProvider>
      </MemoryRouter>,
    );
    const dialog = await screen.findByTestId("repo-drawer");
    const user = userEvent.setup();
    await user.click(containerOf(dialog));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(dialog);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("traps focus in a native modal dialog and returns focus to the trigger on close", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const user = userEvent.setup();
    render(<LauncherAndDrawer />);
    const launcher = screen.getByTestId("launcher");
    await user.click(launcher);
    expect(launcher).toHaveFocus();
    const dialog = await screen.findByTestId("repo-drawer");
    expect(showModal).toHaveBeenCalled();

    fireEvent(dialog, new Event("cancel"));
    expect(close).toHaveBeenCalled();
    expect(screen.queryByTestId("repo-drawer")).not.toBeInTheDocument();
    expect(launcher).toHaveFocus();
  });
});

const AARDVARK_KEY = "acme/aardvark";
const OTHER_REPO_KEYS = [AARDVARK_KEY, STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY];

const EXPECTED_DRAWER_ORDER: Record<DefaultKeyState, string[]> = {
  equal: [STUB_DEFAULT_ENGINE_KEY, AARDVARK_KEY, LISTED_DEFAULT_KEY],
  other: [LISTED_DEFAULT_KEY, AARDVARK_KEY, STUB_DEFAULT_ENGINE_KEY],
};

const RESOLVED_DEFAULT: Record<DefaultKeyState, string> = {
  equal: STUB_DEFAULT_ENGINE_KEY,
  other: LISTED_DEFAULT_KEY,
};

const LONE_BUILT_IN_SHOWS_SWITCHER: Record<DefaultKeyState, boolean> = {
  equal: false,
  other: true,
};

function stubListing(
  keys: readonly string[],
  defaultKey: string | undefined,
  makeSummary: typeof makeEngineSummaryFor = makeEngineSummaryFor,
): void {
  server.use(
    engineListingHandler({
      engines: keys.map((key) => makeSummary(key, { posture: "autonomous" })),
      defaultKey,
    }),
    http.get("/api/build-chat/sessions", () => HttpResponse.json([])),
    http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
  );
}

function ListingProbe() {
  const { loaded } = useEngines();
  return <span data-testid="listing-loaded">{String(loaded)}</span>;
}

async function renderDrawerAfterListing(engineKey: string): Promise<void> {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <ShellExploreProvider>
        <ListingProbe />
        <RepoDrawer engineKey={engineKey} onClose={() => undefined} />
      </ShellExploreProvider>
    </MemoryRouter>,
  );
  await waitFor(() => {
    expect(screen.getByTestId("listing-loaded")).toHaveTextContent("true");
  });
}

function drawerRepoLinks(): HTMLElement[] {
  return within(screen.getByTestId("repo-drawer-switcher")).getAllByRole("link");
}

describe("RepoDrawer — listed default repository", () => {
  it.each(DEFAULT_KEY_STATES)(
    "orders the resolved default repository first and links it by its key (%s)",
    async (state, defaultKey) => {
      stubListing(OTHER_REPO_KEYS, defaultKey);
      await renderDrawerAfterListing(AARDVARK_KEY);
      const links = drawerRepoLinks();
      expect(links.map((link) => link.dataset.testid)).toEqual(
        EXPECTED_DRAWER_ORDER[state].map((key) => `repo-drawer-repo-${key}`),
      );
      expect(links[0]).toHaveAttribute("href", `/repos/${RESOLVED_DEFAULT[state]}`);
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "renders no repository list when the only registered repository is an unconfigured resolved default (%s)",
    async (state, defaultKey) => {
      stubListing([RESOLVED_DEFAULT[state]], defaultKey);
      await renderDrawerAfterListing(RESOLVED_DEFAULT[state]);
      expect(screen.queryByTestId("repo-drawer-switcher")).not.toBeInTheDocument();
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "lists the lone configured default as the current repository when it is the drawer's own (%s)",
    async (state, defaultKey) => {
      const own = RESOLVED_DEFAULT[state];
      stubListing([own], defaultKey, makeConfiguredEngineSummaryFor);
      await renderDrawerAfterListing(own);
      const links = drawerRepoLinks();
      expect(links.map((link) => link.dataset.testid)).toEqual([`repo-drawer-repo-${own}`]);
      expect(links[0]).toHaveAttribute("href", `/repos/${own}`);
      expect(links[0]).toHaveAttribute("aria-current", "true");
    },
  );

  it("lists the lone configured default without marking it when the drawer is scoped to another repository", async () => {
    stubListing([LISTED_DEFAULT_KEY], LISTED_DEFAULT_KEY, makeConfiguredEngineSummaryFor);
    await renderDrawerAfterListing(AARDVARK_KEY);
    const links = drawerRepoLinks();
    expect(links.map((link) => link.dataset.testid)).toEqual([`repo-drawer-repo-${LISTED_DEFAULT_KEY}`]);
    expect(links[0]).not.toHaveAttribute("aria-current");
  });

  it.each(DEFAULT_KEY_STATES)(
    "renders a repository list for a lone built-in repository only when the listed default differs from it (%s)",
    async (state, defaultKey) => {
      stubListing([STUB_DEFAULT_ENGINE_KEY], defaultKey);
      await renderDrawerAfterListing(STUB_DEFAULT_ENGINE_KEY);
      expect(screen.queryByTestId("repo-drawer-switcher") !== null).toBe(
        LONE_BUILT_IN_SHOWS_SWITCHER[state],
      );
    },
  );

  it("marks only the drawer's own repository as current when it is the listed default", async () => {
    stubListing(OTHER_REPO_KEYS, LISTED_DEFAULT_KEY);
    await renderDrawerAfterListing(LISTED_DEFAULT_KEY);
    const marked = drawerRepoLinks().filter((link) => link.hasAttribute("aria-current"));
    expect(marked.map((link) => link.dataset.testid)).toEqual([
      `repo-drawer-repo-${LISTED_DEFAULT_KEY}`,
    ]);
    expect(screen.getByTestId(`conversation-rail-${LISTED_DEFAULT_KEY}`)).toBeInTheDocument();
  });
});
