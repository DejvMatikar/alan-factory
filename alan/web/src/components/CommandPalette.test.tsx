import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { REPO_SCOPE_STORAGE_KEY } from "../hooks/useRepoScope";
import { ShellExploreProvider, useShellExplore } from "../shell/ShellExploreProvider";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  gatedEngineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
  type DefaultKeyState,
} from "../test/engineFixtures";
import { server } from "../test/server";
import { makeWorkspaceDetail } from "../test/workspaceFixtures";
import CommandPalette from "./CommandPalette";
import type { CommandPaletteStep } from "./commandPaletteContext";

const MAC_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";
const LINUX_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function RouteProbe({ testId }: Readonly<{ testId: string }>) {
  return <div data-testid={testId}>reached</div>;
}

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="location">{`${pathname}${search}`}</div>;
}

function ExploreTargetProbe() {
  const { target } = useShellExplore();
  return <div data-testid="explore-target">{target?.repoKey ?? "none"}</div>;
}

function mockEngines(repos: { owner: string; repo: string }[]): void {
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        default_engine_key: STUB_DEFAULT_ENGINE_KEY,
        engines: repos.map(({ owner, repo }) => ({ repo: { owner, repo }, posture: "autonomous" })),
      }),
    ),
  );
}

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(LINUX_UA);
});

function renderPalette(
  step: CommandPaletteStep,
  opts: { onAdvance?: (s: CommandPaletteStep) => void; onClose?: () => void; path?: string } = {},
) {
  const onAdvance = opts.onAdvance ?? vi.fn();
  const onClose = opts.onClose ?? vi.fn();
  const palette = <CommandPalette step={step} onAdvance={onAdvance} onClose={onClose} />;
  render(
    <MemoryRouter initialEntries={[opts.path ?? "/"]}>
      <ShellExploreProvider>
        <ExploreTargetProbe />
        <LocationProbe />
        <Routes>
          <Route path="/" element={palette} />
          <Route path="/repos/:owner/:repo" element={palette} />
          <Route path="/workspaces/:taskId" element={palette} />
          <Route path="/repos/:owner/:repo/build" element={<RouteProbe testId="repo-build-page" />} />
          <Route path="/build" element={<RouteProbe testId="build-page" />} />
          <Route path="/explore/:consultant" element={<RouteProbe testId="explore-page" />} />
        </Routes>
      </ShellExploreProvider>
    </MemoryRouter>,
  );
  return { onAdvance, onClose };
}

describe("CommandPalette — root step", () => {
  it("renders Build and Explore, focused on the filter input", () => {
    renderPalette({ name: "root" });
    expect(screen.getByTestId("palette-command-build")).toHaveTextContent("Build");
    expect(screen.getByTestId("palette-command-explore")).toHaveTextContent("Explore");
    expect(screen.getByTestId("list-chooser-filter")).toHaveFocus();
  });

  it("filters commands by typed text", async () => {
    renderPalette({ name: "root" });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("list-chooser-filter"), "buil");
    expect(screen.getByTestId("palette-command-build")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-command-explore")).not.toBeInTheDocument();
  });

  it("selecting Build advances to the build-repo step", async () => {
    const { onAdvance } = renderPalette({ name: "root" });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("palette-command-build"));
    expect(onAdvance).toHaveBeenCalledOnce();
    expect(onAdvance).toHaveBeenCalledWith({ name: "build-repo" });
  });

  it("selecting Explore advances to the explore-consultant step", async () => {
    const { onAdvance } = renderPalette({ name: "root" });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("palette-command-explore"));
    expect(onAdvance).toHaveBeenCalledOnce();
    expect(onAdvance).toHaveBeenCalledWith({ name: "explore-consultant" });
  });

  it("Escape closes the palette", () => {
    const { onClose } = renderPalette({ name: "root" });
    fireEvent(screen.getByTestId("command-palette"), new Event("cancel"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("CommandPalette — root step shortcut hints", () => {
  it("shows the direct Build and Explore shortcuts on a non-Apple platform", () => {
    renderPalette({ name: "root" });
    const build = screen.getByTestId("palette-command-build");
    const explore = screen.getByTestId("palette-command-explore");
    expect(within(build).getByText("Ctrl Alt B")).toHaveAttribute("aria-hidden", "true");
    expect(within(explore).getByText("Ctrl Alt C")).toHaveAttribute("aria-hidden", "true");
  });

  it("speaks the direct shortcuts in each command's accessible name", () => {
    renderPalette({ name: "root" });
    expect(screen.getByTestId("palette-command-build")).toHaveAccessibleName(
      /Build.*shortcut Control Alt B/,
    );
    expect(screen.getByTestId("palette-command-explore")).toHaveAccessibleName(
      /Explore.*shortcut Control Alt C/,
    );
  });

  it("shows the direct shortcuts with Apple glyphs on an Apple platform", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(MAC_UA);
    renderPalette({ name: "root" });
    expect(within(screen.getByTestId("palette-command-build")).getByText("⌥⌘B")).toBeInTheDocument();
    expect(within(screen.getByTestId("palette-command-explore")).getByText("⌥⌘C")).toBeInTheDocument();
    expect(screen.getByTestId("palette-command-build")).toHaveAccessibleName(
      /shortcut Option Command B/,
    );
  });
});

describe("CommandPalette — root step without a current repository", () => {
  it("offers no repository-scoped commands", () => {
    renderPalette({ name: "root" });
    expect(screen.queryByTestId("palette-command-build-scoped")).not.toBeInTheDocument();
    expect(screen.queryByTestId("palette-command-explore-scoped")).not.toBeInTheDocument();
  });

  it("offers no repository-scoped commands when the mirrored repository is not a connected one", () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/gone");
    renderPalette({ name: "root" });
    expect(screen.queryByTestId("palette-command-build-scoped")).not.toBeInTheDocument();
  });
});

describe("CommandPalette — root step with a current repository", () => {
  const CONNECTED = [
    { owner: "DejvMatikar", repo: "alan" },
    { owner: "acme", repo: "widget" },
  ];

  async function renderScoped(path = "/repos/acme/widget") {
    mockEngines(CONNECTED);
    const rendered = renderPalette({ name: "root" }, { path });
    await screen.findByTestId("palette-command-build-scoped");
    return rendered;
  }

  it("lists Build and Explore first, then the scoped Build and Explore for the repository", async () => {
    await renderScoped();
    const rows = screen.getAllByTestId(/^palette-command-/).map((row) => row.dataset.testid);
    expect(rows).toEqual([
      "palette-command-build",
      "palette-command-explore",
      "palette-command-build-scoped",
      "palette-command-explore-scoped",
    ]);
  });

  it("labels the scoped commands with the repository's title", async () => {
    await renderScoped();
    expect(screen.getByTestId("palette-command-build-scoped")).toHaveTextContent("Build in Widget");
    expect(screen.getByTestId("palette-command-explore-scoped")).toHaveTextContent("Explore Widget");
  });

  it("falls back to the repository key when the repository has no switcher entry", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }]);
    renderPalette({ name: "root" }, { path: "/repos/acme/widget" });
    expect(await screen.findByTestId("palette-command-build-scoped")).toHaveTextContent(
      "Build in acme/widget",
    );
    expect(screen.getByTestId("palette-command-explore-scoped")).toHaveTextContent(
      "Explore acme/widget",
    );
  });

  it("shows Ctrl Shift X and Ctrl Shift E on the scoped commands", async () => {
    await renderScoped();
    const build = screen.getByTestId("palette-command-build-scoped");
    const explore = screen.getByTestId("palette-command-explore-scoped");
    expect(within(build).getByText("Ctrl Shift X")).toBeInTheDocument();
    expect(within(explore).getByText("Ctrl Shift E")).toBeInTheDocument();
    expect(build).toHaveAccessibleName(/shortcut Control Shift X/);
  });

  it("shows the Apple glyphs on the scoped commands on an Apple platform", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(MAC_UA);
    await renderScoped();
    const build = screen.getByTestId("palette-command-build-scoped");
    const explore = screen.getByTestId("palette-command-explore-scoped");
    expect(within(build).getByText("⌃⇧X")).toBeInTheDocument();
    expect(within(explore).getByText("⌃⇧E")).toBeInTheDocument();
  });

  it("appears from the mirrored repository on a page outside the repository routes", async () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/widget");
    await renderScoped("/");
    expect(screen.getByTestId("palette-command-explore-scoped")).toHaveTextContent("Explore Widget");
  });

  it("filters the scoped commands by label", async () => {
    await renderScoped();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("list-chooser-filter"), "build in");
    expect(screen.getByTestId("palette-command-build-scoped")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-command-explore-scoped")).not.toBeInTheDocument();
    expect(screen.queryByTestId("palette-command-build")).not.toBeInTheDocument();
  });

  it("filters the scoped commands by repository key", async () => {
    await renderScoped();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("list-chooser-filter"), "acme/wid");
    expect(screen.getByTestId("palette-command-build-scoped")).toBeInTheDocument();
    expect(screen.getByTestId("palette-command-explore-scoped")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-command-build")).not.toBeInTheDocument();
    expect(screen.queryByTestId("palette-command-explore")).not.toBeInTheDocument();
  });

  it("choosing the scoped Build closes the palette and starts a build conversation in that repository", async () => {
    const { onClose, onAdvance } = await renderScoped();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("palette-command-build-scoped"));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onAdvance).not.toHaveBeenCalled();
    expect(await screen.findByTestId("repo-build-page")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/repos/acme/widget/build?engine=acme%2Fwidget",
    );
  });

  it("choosing the scoped Explore closes the palette and opens Explore for that repository", async () => {
    const { onClose, onAdvance } = await renderScoped();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("palette-command-explore-scoped"));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onAdvance).not.toHaveBeenCalled();
    expect(screen.getByTestId("explore-target")).toHaveTextContent("acme/widget");
  });
});

describe("CommandPalette — build-repo step", () => {
  it("renders the connected repository and states the consequence", async () => {
    renderPalette({ name: "build-repo" });
    expect(await screen.findByTestId("palette-repo-DejvMatikar/alan")).toBeInTheDocument();
    expect(
      screen.getByText("Choosing a repository starts a build conversation for it."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("list-chooser-filter")).toHaveFocus();
  });

  it("filters repositories by typed text", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [{ repo: { owner: "acme", repo: "widgets" }, posture: "observe" }],
        }),
      ),
    );
    renderPalette({ name: "build-repo" });
    expect(await screen.findByTestId("palette-repo-acme/widgets")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("list-chooser-filter"), "widg");
    expect(screen.getByTestId("palette-repo-acme/widgets")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-repo-DejvMatikar/alan")).not.toBeInTheDocument();
  });

  it("filters repositories by owner, not just the repo title", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [{ repo: { owner: "acme", repo: "widgets" }, posture: "observe" }],
        }),
      ),
    );
    renderPalette({ name: "build-repo" });
    expect(await screen.findByTestId("palette-repo-acme/widgets")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("list-chooser-filter"), "acme");
    expect(screen.getByTestId("palette-repo-acme/widgets")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-repo-DejvMatikar/alan")).not.toBeInTheDocument();
  });

  it("selecting a repository closes the palette and navigates to a build conversation for it", async () => {
    const { onClose } = renderPalette({ name: "build-repo" });
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("palette-repo-DejvMatikar/alan"));
    expect(onClose).toHaveBeenCalledOnce();
    expect(await screen.findByTestId("build-page")).toBeInTheDocument();
  });
});

describe("CommandPalette — explore-consultant step", () => {
  it("renders every served consultant", async () => {
    renderPalette({ name: "explore-consultant" });
    expect(await screen.findByTestId("palette-consultant-research")).toBeInTheDocument();
    expect(screen.getByTestId("palette-consultant-architecture")).toBeInTheDocument();
    expect(screen.getByTestId("list-chooser-filter")).toHaveFocus();
  });

  it("filters consultants by typed text", async () => {
    renderPalette({ name: "explore-consultant" });
    const user = userEvent.setup();
    await screen.findByTestId("palette-consultant-research");
    await user.type(screen.getByTestId("list-chooser-filter"), "research");
    expect(screen.getByTestId("palette-consultant-research")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-consultant-architecture")).not.toBeInTheDocument();
  });

  it("selecting a consultant advances to the explore-repo step for it", async () => {
    const { onAdvance } = renderPalette({ name: "explore-consultant" });
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("palette-consultant-research"));
    expect(onAdvance).toHaveBeenCalledOnce();
    expect(onAdvance).toHaveBeenCalledWith({
      name: "explore-repo",
      consultantKey: "research",
      consultantLabel: "Research",
    });
  });

  it("shows the shared load-error banner and no consultant items when the list fails to load", async () => {
    server.use(
      http.get("/api/explore-chat/consultants", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderPalette({ name: "explore-consultant" });
    expect(await screen.findByTestId("consultants-load-error")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-consultant-research")).not.toBeInTheDocument();
  });
});

describe("CommandPalette — explore-repo step", () => {
  const step: CommandPaletteStep = {
    name: "explore-repo",
    consultantKey: "research",
    consultantLabel: "Research",
  };

  it("renders the connected repository under the chosen consultant's title", async () => {
    renderPalette(step);
    expect(screen.getByRole("heading", { name: "Explore / Research" })).toBeInTheDocument();
    expect(await screen.findByTestId("palette-repo-DejvMatikar/alan")).toBeInTheDocument();
    expect(screen.getByTestId("list-chooser-filter")).toHaveFocus();
  });

  it("selecting a repository closes the palette and navigates to an explore conversation for it", async () => {
    const { onClose } = renderPalette(step);
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("palette-repo-DejvMatikar/alan"));
    expect(onClose).toHaveBeenCalledOnce();
    expect(await screen.findByTestId("explore-page")).toBeInTheDocument();
  });
});

const AARDVARK_KEY = "acme/aardvark";

const REPOS_WITHOUT_BUILT_IN_ORDER: Record<DefaultKeyState, string[]> = {
  equal: [STUB_DEFAULT_ENGINE_KEY, AARDVARK_KEY],
  other: [LISTED_DEFAULT_KEY, AARDVARK_KEY],
};

const REPOS_WITH_BUILT_IN_ORDER: Record<DefaultKeyState, string[]> = {
  equal: [STUB_DEFAULT_ENGINE_KEY, AARDVARK_KEY],
  other: [LISTED_DEFAULT_KEY, AARDVARK_KEY, STUB_DEFAULT_ENGINE_KEY],
};

const SCOPED_BUILD_LABEL: Record<DefaultKeyState, string> = {
  equal: "Build in Alan",
  other: "Build in Listed Default",
};

function stubListing(keys: readonly string[], defaultKey: string | undefined): void {
  server.use(
    engineListingHandler({ engines: keys.map((key) => makeEngineSummaryFor(key)), defaultKey }),
  );
}

function listedRepoKeys(): (string | undefined)[] {
  return screen
    .getAllByTestId(/^palette-repo-/)
    .map((row) => row.dataset.testid?.replace("palette-repo-", ""));
}

describe("CommandPalette — repository chooser with a listed default", () => {
  it.each(DEFAULT_KEY_STATES)(
    "lists the resolved default first even when it is not registered, and no other unregistered key (%s)",
    async (state, defaultKey) => {
      stubListing([AARDVARK_KEY], defaultKey);
      renderPalette({ name: "build-repo" });
      await waitFor(() => {
        expect(listedRepoKeys()).toEqual(REPOS_WITHOUT_BUILT_IN_ORDER[state]);
      });
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "lists the built-in key as an ordinary entry only when it is registered (%s)",
    async (state, defaultKey) => {
      stubListing([AARDVARK_KEY, STUB_DEFAULT_ENGINE_KEY], defaultKey);
      renderPalette({ name: "build-repo" });
      await waitFor(() => {
        expect(listedRepoKeys()).toEqual(REPOS_WITH_BUILT_IN_ORDER[state]);
      });
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "lists the same repositories under the explore-repo step (%s)",
    async (state, defaultKey) => {
      stubListing([AARDVARK_KEY], defaultKey);
      renderPalette({ name: "explore-repo", consultantKey: "research", consultantLabel: "Research" });
      await waitFor(() => {
        expect(listedRepoKeys()).toEqual(REPOS_WITHOUT_BUILT_IN_ORDER[state]);
      });
    },
  );

  it("starts a build conversation for the listed default when it is chosen", async () => {
    stubListing([AARDVARK_KEY], LISTED_DEFAULT_KEY);
    renderPalette({ name: "build-repo" });
    const user = userEvent.setup();
    await user.click(await screen.findByTestId(`palette-repo-${LISTED_DEFAULT_KEY}`));
    expect(await screen.findByTestId("build-page")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/build?engine=${encodeURIComponent(LISTED_DEFAULT_KEY)}`,
    );
  });

  it("filters the listed default by its key", async () => {
    stubListing([AARDVARK_KEY], LISTED_DEFAULT_KEY);
    renderPalette({ name: "build-repo" });
    const user = userEvent.setup();
    await screen.findByTestId(`palette-repo-${LISTED_DEFAULT_KEY}`);
    await user.type(screen.getByTestId("list-chooser-filter"), "listed-default");
    expect(listedRepoKeys()).toEqual([LISTED_DEFAULT_KEY]);
  });
});

describe("CommandPalette — repository chooser before the engine list loads", () => {
  it("offers no repository until the list loads, then lists the listed default first in place", async () => {
    const { handler, release } = gatedEngineListingHandler({
      engines: [makeEngineSummaryFor(AARDVARK_KEY)],
      defaultKey: LISTED_DEFAULT_KEY,
    });
    server.use(handler);
    renderPalette({ name: "build-repo" });
    const dialog = screen.getByTestId("command-palette");
    expect(screen.queryAllByTestId(/^palette-repo-/)).toEqual([]);

    release();
    const listed = await screen.findByTestId(`palette-repo-${LISTED_DEFAULT_KEY}`);
    expect(listed).toBeEnabled();
    expect(listedRepoKeys()).toEqual([LISTED_DEFAULT_KEY, AARDVARK_KEY]);
    expect(screen.getByTestId("command-palette")).toBe(dialog);
  });

  it("never offers the built-in key while the list is loading, whatever the list later names", async () => {
    const { handler, release } = gatedEngineListingHandler({
      engines: [makeEngineSummaryFor(AARDVARK_KEY)],
      defaultKey: STUB_DEFAULT_ENGINE_KEY,
    });
    server.use(handler);
    renderPalette({ name: "build-repo" });
    expect(screen.queryByTestId(`palette-repo-${STUB_DEFAULT_ENGINE_KEY}`)).not.toBeInTheDocument();
    release();
    await screen.findByTestId(`palette-repo-${AARDVARK_KEY}`);
    expect(listedRepoKeys()).toEqual([STUB_DEFAULT_ENGINE_KEY, AARDVARK_KEY]);
  });
});

describe("CommandPalette — repository chooser with no listed default", () => {
  it("lists only the registered repositories", async () => {
    stubListing([AARDVARK_KEY], undefined);
    renderPalette({ name: "build-repo" });
    await screen.findByTestId(`palette-repo-${AARDVARK_KEY}`);
    expect(listedRepoKeys()).toEqual([AARDVARK_KEY]);
  });

  it("offers no repository when none is registered", async () => {
    stubListing([], undefined);
    renderPalette({ name: "build-repo" });
    await waitFor(() => {
      expect(screen.queryAllByTestId(/^palette-repo-/)).toEqual([]);
    });
  });

  it("offers no scoped commands on a workspace with no engine key", async () => {
    stubListing([AARDVARK_KEY], undefined);
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(makeWorkspaceDetail({ engine_key: null }))),
    );
    renderPalette({ name: "root" }, { path: "/workspaces/t1" });
    await screen.findByTestId("palette-command-build");
    expect(screen.queryByTestId("palette-command-build-scoped")).not.toBeInTheDocument();
  });
});

describe("CommandPalette — scoped commands with a listed default", () => {
  it.each(DEFAULT_KEY_STATES)(
    "scopes a workspace with no engine key to the resolved default repository (%s)",
    async (state, defaultKey) => {
      stubListing([AARDVARK_KEY, STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY], defaultKey);
      server.use(
        http.get("/api/workspaces/t1", () =>
          HttpResponse.json(makeWorkspaceDetail({ engine_key: null })),
        ),
      );
      renderPalette({ name: "root" }, { path: "/workspaces/t1" });
      expect(await screen.findByTestId("palette-command-build-scoped")).toHaveTextContent(
        SCOPED_BUILD_LABEL[state],
      );
    },
  );
});
