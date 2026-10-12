import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPaletteContext } from "../components/commandPaletteContext";
import { useEngines } from "../hooks/useEngines";
import { REPO_SCOPE_STORAGE_KEY, useRepoScope } from "../hooks/useRepoScope";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  makeConfiguredEngineSummaryFor,
  makeEngineSummaryFor,
} from "../test/engineFixtures";
import { server } from "../test/server";
import { makeWorkspaceDetail } from "../test/workspaceFixtures";
import { ShellExploreProvider, useShellExplore } from "./ShellExploreProvider";
import ShellShortcuts from "./ShellShortcuts";

function ScopeProbe() {
  const { engines, defaultEngineKey, loaded } = useEngines();
  const { key } = useRepoScope(engines, defaultEngineKey);
  const { pathname, search } = useLocation();
  const { target } = useShellExplore();
  return (
    <>
      <div data-testid="engines-loaded">{String(loaded)}</div>
      <div data-testid="scope">{key ?? "none"}</div>
      <div data-testid="location">{`${pathname}${search}`}</div>
      <div data-testid="explore-target">{target?.repoKey ?? "none"}</div>
    </>
  );
}

function mockEngines(repos: { owner: string; repo: string }[]): void {
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: repos.map(({ owner, repo }) => ({ repo: { owner, repo }, posture: "autonomous" })),
      }),
    ),
  );
}

function mockWorkspace(engineKey: string | null): void {
  server.use(
    http.get("/api/workspaces/t1", () => HttpResponse.json(makeWorkspaceDetail({ engine_key: engineKey }))),
  );
}

function renderAt(path: string, extra: ReactNode = null) {
  const openPalette = vi.fn();
  render(
    <MemoryRouter initialEntries={[path]}>
      <ShellExploreProvider>
        <CommandPaletteContext.Provider value={{ openPalette }}>
          <ShellShortcuts />
          <ScopeProbe />
          <Routes>
            <Route path="*" element={<div data-testid="page">{extra}</div>} />
          </Routes>
        </CommandPaletteContext.Provider>
      </ShellExploreProvider>
    </MemoryRouter>,
  );
  return { openPalette };
}

const WIDGET = [
  { owner: "DejvMatikar", repo: "alan" },
  { owner: "acme", repo: "widget" },
];

function pressBuild(target: Document | Element = document): boolean {
  return fireEvent.keyDown(target, { code: "KeyX", ctrlKey: true, shiftKey: true });
}

function pressExplore(target: Document | Element = document): boolean {
  return fireEvent.keyDown(target, { code: "KeyE", ctrlKey: true, shiftKey: true });
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("ShellShortcuts — on a repository page", () => {
  it("Ctrl+Shift+X starts a build conversation in that repository", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/repos/acme/widget");
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    pressBuild();
    expect(screen.getByTestId("location")).toHaveTextContent("/repos/acme/widget/build?engine=acme%2Fwidget");
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("Ctrl+Shift+E opens Explore for that repository", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/repos/acme/widget");
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    pressExplore();
    expect(screen.getByTestId("explore-target")).toHaveTextContent("acme/widget");
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("prevents the browser default for the shortcuts it handles", async () => {
    mockEngines(WIDGET);
    renderAt("/repos/acme/widget");
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    expect(pressBuild()).toBe(false);
    expect(pressExplore()).toBe(false);
  });

  it("acts even when Alt or Meta are held with Ctrl+Shift", async () => {
    mockEngines(WIDGET);
    renderAt("/repos/acme/widget");
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    fireEvent.keyDown(document, { code: "KeyE", ctrlKey: true, shiftKey: true, altKey: true, metaKey: true });
    expect(screen.getByTestId("explore-target")).toHaveTextContent("acme/widget");
  });

  it("ignores an unrelated Ctrl+Shift key", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/repos/acme/widget");
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    const notCancelled = fireEvent.keyDown(document, { code: "KeyZ", ctrlKey: true, shiftKey: true });
    expect(notCancelled).toBe(true);
    expect(screen.getByTestId("explore-target")).toHaveTextContent("none");
    expect(screen.getByTestId("location")).toHaveTextContent("/repos/acme/widget");
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("ignores X and E without Shift", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/repos/acme/widget");
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    fireEvent.keyDown(document, { code: "KeyX", ctrlKey: true });
    fireEvent.keyDown(document, { code: "KeyE", ctrlKey: true });
    expect(screen.getByTestId("explore-target")).toHaveTextContent("none");
    expect(openPalette).not.toHaveBeenCalled();
  });
});

describe.each([
  ["detail", "/workspaces/t1"],
  ["preparation chat", "/workspaces/t1/prep-chat"],
  ["run full view", "/workspaces/t1/invocations/inv_001"],
] as const)("ShellShortcuts — on a workspace %s page", (_page, path) => {
  it("Ctrl+Shift+X starts a build conversation in the workspace's own repository", async () => {
    mockEngines(WIDGET);
    mockWorkspace("acme/widget");
    const { openPalette } = renderAt(path);
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    pressBuild();
    expect(screen.getByTestId("location")).toHaveTextContent("/repos/acme/widget/build?engine=acme%2Fwidget");
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("Ctrl+Shift+E opens Explore for the workspace's own repository", async () => {
    mockEngines(WIDGET);
    mockWorkspace("acme/widget");
    const { openPalette } = renderAt(path);
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    pressExplore();
    expect(screen.getByTestId("explore-target")).toHaveTextContent("acme/widget");
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("targets the workspace's repository even when the session mirror names another", async () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "DejvMatikar/alan");
    mockEngines(WIDGET);
    mockWorkspace("acme/widget");
    renderAt(path);
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    pressBuild();
    expect(screen.getByTestId("location")).toHaveTextContent("/repos/acme/widget/build");
  });
});

describe("ShellShortcuts — with no repository to find", () => {
  it("Ctrl+Shift+X opens the palette's repository chooser for Build", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/health");
    await screen.findByText("none", { selector: '[data-testid="scope"]' });
    pressBuild();
    expect(openPalette).toHaveBeenCalledExactlyOnceWith({ name: "build-repo" });
    expect(screen.getByTestId("location")).toHaveTextContent("/health");
  });

  it("Ctrl+Shift+E opens the palette's consultant chooser for Explore", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/health");
    await screen.findByText("none", { selector: '[data-testid="scope"]' });
    pressExplore();
    expect(openPalette).toHaveBeenCalledExactlyOnceWith({ name: "explore-consultant" });
    expect(screen.getByTestId("explore-target")).toHaveTextContent("none");
  });

  it("falls back to the chooser on a workspace page whose repository is not among the visible ones", async () => {
    mockEngines(WIDGET);
    mockWorkspace("acme/hidden");
    const { openPalette } = renderAt("/workspaces/t1");
    await screen.findByText("none", { selector: '[data-testid="scope"]' });
    pressBuild();
    pressExplore();
    expect(openPalette).toHaveBeenNthCalledWith(1, { name: "build-repo" });
    expect(openPalette).toHaveBeenNthCalledWith(2, { name: "explore-consultant" });
  });

  it("falls back to the chooser on a workspace page whose workspace cannot be read", async () => {
    mockEngines(WIDGET);
    const requests = { count: 0 };
    server.use(
      http.get("/api/workspaces/t1", () => {
        requests.count += 1;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const { openPalette } = renderAt("/workspaces/t1");
    await waitFor(() => {
      expect(requests.count).toBe(1);
    });
    await act(async () => {
      await Promise.resolve();
    });
    pressBuild();
    expect(openPalette).toHaveBeenCalledExactlyOnceWith({ name: "build-repo" });
  });

  it("prevents the browser default even when it falls back to the chooser", async () => {
    mockEngines(WIDGET);
    renderAt("/health");
    await screen.findByText("none", { selector: '[data-testid="scope"]' });
    expect(pressBuild()).toBe(false);
    expect(pressExplore()).toBe(false);
  });
});

describe("ShellShortcuts — guards", () => {
  it("does nothing while the user is typing in a text field", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/repos/acme/widget", <input data-testid="field" />);
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    const notCancelledBuild = pressBuild(screen.getByTestId("field"));
    const notCancelledExplore = pressExplore(screen.getByTestId("field"));
    expect(notCancelledBuild).toBe(true);
    expect(notCancelledExplore).toBe(true);
    expect(screen.getByTestId("location")).toHaveTextContent("/repos/acme/widget");
    expect(screen.getByTestId("explore-target")).toHaveTextContent("none");
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("does nothing without a repository while the user is typing in a text field", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/health", <textarea data-testid="field" />);
    await screen.findByText("none", { selector: '[data-testid="scope"]' });
    pressBuild(screen.getByTestId("field"));
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("does nothing while another dialog is open", async () => {
    mockEngines(WIDGET);
    const { openPalette } = renderAt("/repos/acme/widget", <dialog open>open</dialog>);
    await screen.findByText("acme/widget", { selector: '[data-testid="scope"]' });
    pressBuild();
    pressExplore();
    expect(screen.getByTestId("location")).toHaveTextContent("/repos/acme/widget");
    expect(screen.getByTestId("explore-target")).toHaveTextContent("none");
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("stops listening once it unmounts", () => {
    mockEngines(WIDGET);
    const openPalette = vi.fn();
    const { unmount } = render(
      <MemoryRouter initialEntries={["/health"]}>
        <ShellExploreProvider>
          <CommandPaletteContext.Provider value={{ openPalette }}>
            <ShellShortcuts />
          </CommandPaletteContext.Provider>
        </ShellExploreProvider>
      </MemoryRouter>,
    );
    unmount();
    pressBuild();
    expect(openPalette).not.toHaveBeenCalled();
  });
});

describe("ShellShortcuts — on a workspace page with no engine key", () => {
  it.each(DEFAULT_KEY_STATES)("Ctrl+Shift+X starts a build conversation in the resolved default repository (%s)", async (_state, listed) => {
    const defaultKey = listed;
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(defaultKey), makeEngineSummaryFor("acme/widget")],
        defaultKey: listed,
      }),
    );
    mockWorkspace(null);
    const { openPalette } = renderAt("/workspaces/t1");
    await screen.findByText(defaultKey, { selector: '[data-testid="scope"]' });
    pressBuild();
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/repos/${defaultKey}/build?engine=${encodeURIComponent(defaultKey)}`,
    );
    expect(openPalette).not.toHaveBeenCalled();
  });

  it.each(DEFAULT_KEY_STATES)("Ctrl+Shift+E opens Explore for the resolved default repository (%s)", async (_state, listed) => {
    const defaultKey = listed;
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(defaultKey), makeEngineSummaryFor("acme/widget")],
        defaultKey: listed,
      }),
    );
    mockWorkspace(null);
    const { openPalette } = renderAt("/workspaces/t1");
    await screen.findByText(defaultKey, { selector: '[data-testid="scope"]' });
    pressExplore();
    expect(screen.getByTestId("explore-target")).toHaveTextContent(defaultKey);
    expect(openPalette).not.toHaveBeenCalled();
  });
});

describe("ShellShortcuts — on a workspace page with no engine key and no listed default", () => {
  it("resolves no scope, so both shortcuts open the palette's choosers", async () => {
    let workspaceRequests = 0;
    server.use(
      engineListingHandler({ engines: [makeEngineSummaryFor("acme/widget")] }),
      http.get("/api/workspaces/t1", () => {
        workspaceRequests += 1;
        return HttpResponse.json(makeWorkspaceDetail({ engine_key: null }));
      }),
    );
    const { openPalette } = renderAt("/workspaces/t1");
    await waitFor(() => {
      expect(workspaceRequests).toBeGreaterThan(0);
      expect(screen.getByTestId("engines-loaded")).toHaveTextContent("true");
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByTestId("scope")).toHaveTextContent("none");
    pressBuild();
    pressExplore();
    expect(openPalette).toHaveBeenNthCalledWith(1, { name: "build-repo" });
    expect(openPalette).toHaveBeenNthCalledWith(2, { name: "explore-consultant" });
  });
});

function mockLoneConfiguredDefault(defaultKey: string): string {
  server.use(
    engineListingHandler({ engines: [makeConfiguredEngineSummaryFor(defaultKey)], defaultKey }),
  );
  return defaultKey;
}

function GoToHealth() {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      data-testid="go-health"
      onClick={() => {
        void navigate("/health");
      }}
    >
      health
    </button>
  );
}

describe("ShellShortcuts — on a single-repo copy", () => {
  it.each(DEFAULT_KEY_STATES)("Ctrl+Shift+X on the default's workspace page starts a build conversation there (%s)", async (_state, listed) => {
    const defaultKey = mockLoneConfiguredDefault(listed);
    mockWorkspace(null);
    const { openPalette } = renderAt("/workspaces/t1");
    await screen.findByText(defaultKey, { selector: '[data-testid="scope"]' });
    pressBuild();
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/repos/${defaultKey}/build?engine=${encodeURIComponent(defaultKey)}`,
    );
    expect(openPalette).not.toHaveBeenCalled();
  });

  it.each(DEFAULT_KEY_STATES)("Ctrl+Shift+E on the default's workspace page opens Explore for it (%s)", async (_state, listed) => {
    const defaultKey = mockLoneConfiguredDefault(listed);
    mockWorkspace(defaultKey);
    const { openPalette } = renderAt("/workspaces/t1");
    await screen.findByText(defaultKey, { selector: '[data-testid="scope"]' });
    pressExplore();
    expect(screen.getByTestId("explore-target")).toHaveTextContent(defaultKey);
    expect(openPalette).not.toHaveBeenCalled();
  });

  it.each(DEFAULT_KEY_STATES)("Ctrl+Shift+X on Health after the dashboard was visited in the same tab starts a build conversation (%s)", async (_state, listed) => {
    const defaultKey = mockLoneConfiguredDefault(listed);
    const { openPalette } = renderAt(`/repos/${defaultKey}`, <GoToHealth />);
    await screen.findByText(defaultKey, { selector: '[data-testid="scope"]' });
    fireEvent.click(screen.getByTestId("go-health"));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/health");
    });
    expect(screen.getByTestId("scope")).toHaveTextContent(defaultKey);
    pressBuild();
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/repos/${defaultKey}/build?engine=${encodeURIComponent(defaultKey)}`,
    );
    expect(openPalette).not.toHaveBeenCalled();
  });

  it.each(DEFAULT_KEY_STATES)("Ctrl+Shift+E on Health after the dashboard was visited in the same tab opens Explore (%s)", async (_state, listed) => {
    const defaultKey = mockLoneConfiguredDefault(listed);
    const { openPalette } = renderAt(`/repos/${defaultKey}`, <GoToHealth />);
    await screen.findByText(defaultKey, { selector: '[data-testid="scope"]' });
    fireEvent.click(screen.getByTestId("go-health"));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/health");
    });
    pressExplore();
    expect(screen.getByTestId("explore-target")).toHaveTextContent(defaultKey);
    expect(openPalette).not.toHaveBeenCalled();
  });

  it.each(DEFAULT_KEY_STATES)("opens the palette's choosers on a fresh Health page with no recorded scope (%s)", async (_state, listed) => {
    mockLoneConfiguredDefault(listed);
    const { openPalette } = renderAt("/health");
    await screen.findByText("none", { selector: '[data-testid="scope"]' });
    pressBuild();
    pressExplore();
    expect(openPalette).toHaveBeenNthCalledWith(1, { name: "build-repo" });
    expect(openPalette).toHaveBeenNthCalledWith(2, { name: "explore-consultant" });
  });
});
