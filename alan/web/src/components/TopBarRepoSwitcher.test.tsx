import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { accentEdgeHex } from "../lib/repoAccent";
import { useEngines } from "../hooks/useEngines";
import { REPO_SCOPE_STORAGE_KEY } from "../hooks/useRepoScope";
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
import { makeWorkspaceDetail } from "../test/workspaceFixtures";
import TopBarRepoSwitcher, { resolveDrawerEngineKey } from "./TopBarRepoSwitcher";

const ENTRY = { key: "acme/widget", title: "Widget", posture: "autonomous" as const };

describe("resolveDrawerEngineKey", () => {
  it("prefers the scoped key when one is set", () => {
    expect(resolveDrawerEngineKey("acme/widget", [ENTRY])).toBe("acme/widget");
  });

  it("falls back to the first entry when nothing is scoped", () => {
    expect(resolveDrawerEngineKey(null, [ENTRY])).toBe("acme/widget");
  });

  it("resolves to no repository when neither a scope nor an entry is available", () => {
    expect(resolveDrawerEngineKey(null, [])).toBeNull();
  });
});

function mockConversations(): void {
  server.use(
    http.get("/api/build-chat/sessions", () => HttpResponse.json([])),
    http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
  );
}

beforeEach(() => {
  sessionStorage.clear();
});

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

function renderAt(initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/repos/:owner/:repo" element={<TopBarRepoSwitcher />} />
        <Route path="*" element={<TopBarRepoSwitcher />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopBarRepoSwitcher — entries", () => {
  it("renders no entries with nothing connected", async () => {
    mockEngines([]);
    renderAt("/");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId("top-bar-repo-switcher")).not.toBeInTheDocument();
  });

  it("still renders the connect-repo CTA with nothing connected — it never scrolls out of reach behind an empty list", async () => {
    mockEngines([]);
    renderAt("/");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByTestId("connect-repo-cta")).toBeInTheDocument();
  });

  it("renders no drawer trigger with nothing connected — there is nothing to switch to", async () => {
    mockEngines([]);
    renderAt("/health");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId("top-bar-repo-drawer-trigger")).not.toBeInTheDocument();
  });

  it("lists the default repo plus a connected one", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    expect(await screen.findByTestId("top-bar-repo-DejvMatikar/alan")).toHaveTextContent("Alan");
    expect(screen.getByTestId("top-bar-repo-acme/widget")).toHaveTextContent("Widget");
  });

  it("places the connect-repo CTA immediately after the repository list, outside its nav landmark", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    const nav = await screen.findByTestId("top-bar-repo-switcher");
    const cta = screen.getByTestId("connect-repo-cta");
    expect(within(nav).queryByTestId("connect-repo-cta")).not.toBeInTheDocument();
    expect(nav.compareDocumentPosition(cta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("TopBarRepoSwitcher — selection language", () => {
  it("marks the scoped entry with aria-current=true, the accent border, and the repository's own accent text colour", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    const selected = await screen.findByTestId("top-bar-repo-acme/widget");
    expect(selected).toHaveAttribute("aria-current", "true");
    expect(selected.className).toContain("border-[var(--accent-edge)]");
    expect(selected.className).toContain("text-[var(--accent-fg)]");
    expect(selected.style.getPropertyValue("--accent-edge")).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("renders the neutral border/text tokens for the scoped entry when the repository has explicitly chosen no accent", async () => {
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
    const selected = await screen.findByTestId("top-bar-repo-acme/widget");
    expect(selected.className).toContain("border-border");
    expect(selected.className).toContain("text-text-primary");
    expect(selected.className).not.toContain("border-[var(--accent-edge)]");
    expect(selected.className).not.toContain("text-[var(--accent-fg)]");
  });

  it("never uses aria-current=page, since the mark also persists on cross-repository pages", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    const selected = await screen.findByTestId("top-bar-repo-acme/widget");
    expect(selected.getAttribute("aria-current")).toBe("true");
  });

  it("leaves an unselected entry with no aria-current and the muted text-contrast step", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    const unselected = await screen.findByTestId("top-bar-repo-DejvMatikar/alan");
    expect(unselected).not.toHaveAttribute("aria-current");
    expect(unselected.className).toContain("text-text-muted");
    expect(unselected.className).not.toContain("border-[var(--accent-edge)]");
  });

  it("gives the connect-repo CTA a plain-language, hover/focus-revealed description", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    await screen.findByTestId("connect-repo-cta");
    expect(
      screen.getByText("Connect a GitHub repository so alan can start working in it."),
    ).toBeInTheDocument();
  });

  it("wires the connect-repo CTA's description via aria-describedby for assistive technology", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    const cta = await screen.findByTestId("connect-repo-cta");
    const describedBy = cta.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "Connect a GitHub repository so alan can start working in it.",
    );
  });

  it("every entry carries a hover treatment and a visible keyboard-focus ring", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    const entry = await screen.findByTestId("top-bar-repo-acme/widget");
    expect(entry.className).toContain("hover:bg-surface-elevated");
    expect(entry.className).toContain("focus-visible:ring-2");
  });

  it("no entry is ever disabled", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    const entry = await screen.findByTestId("top-bar-repo-acme/widget");
    expect(entry).not.toHaveAttribute("aria-disabled");
  });

  it("resolves entry colours from the unfiltered engine list, so a hidden repository's explicit colour is honoured as taken the same way the page frame honours it", async () => {
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
    );
    renderAt("/repos/acme/widget");
    const entry = await screen.findByTestId("top-bar-repo-acme/widget");
    expect(entry.style.getPropertyValue("--accent-edge")).toBe(accentEdgeHex("teal", "standard"));
  });

  it("hides the switcher list below the desktop breakpoint — the drawer carries mobile switching instead", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    const nav = await screen.findByTestId("top-bar-repo-switcher");
    expect(nav.className).toContain("hidden");
    expect(nav.className).toContain("sm:flex");
    expect(screen.queryByTestId("top-bar-repo-select")).not.toBeInTheDocument();
  });

  it("renders no compact drawer trigger inside the repo shell — RepoShell's own trigger covers it there", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    await screen.findByTestId("top-bar-repo-switcher");
    expect(screen.queryByTestId("top-bar-repo-drawer-trigger")).not.toBeInTheDocument();
  });
});

describe.each([
  ["detail", "/workspaces/t1"],
  ["preparation chat", "/workspaces/t1/prep-chat"],
  ["run full view", "/workspaces/t1/invocations/inv_001"],
] as const)("TopBarRepoSwitcher — on a workspace %s page", (_page, path) => {
  function mockWorkspace(engineKey: string | null): void {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(makeWorkspaceDetail({ engine_key: engineKey }))),
    );
  }

  it("marks the workspace's own repository as current", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    mockWorkspace("acme/widget");
    renderAt(path);
    const selected = await screen.findByTestId("top-bar-repo-acme/widget");
    await waitFor(() => {
      expect(selected).toHaveAttribute("aria-current", "true");
    });
    expect(screen.getByTestId("top-bar-repo-DejvMatikar/alan")).not.toHaveAttribute("aria-current");
  });

  it("marks the default repository for a workspace with no engine key", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    mockWorkspace(null);
    renderAt(path);
    const selected = await screen.findByTestId("top-bar-repo-DejvMatikar/alan");
    await waitFor(() => {
      expect(selected).toHaveAttribute("aria-current", "true");
    });
    expect(screen.getByTestId("top-bar-repo-acme/widget")).not.toHaveAttribute("aria-current");
  });

  it("marks nothing when the workspace's repository is not among the visible ones", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    mockWorkspace("acme/hidden");
    renderAt(path);
    const entry = await screen.findByTestId("top-bar-repo-acme/widget");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(entry).not.toHaveAttribute("aria-current");
    expect(screen.getByTestId("top-bar-repo-DejvMatikar/alan")).not.toHaveAttribute("aria-current");
  });
});

describe("TopBarRepoSwitcher — mobile trigger outside the repo shell", () => {
  it("renders a compact drawer trigger on non-repo routes, opening the shared drawer", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    mockConversations();
    const user = userEvent.setup();
    renderAt("/health");
    const trigger = await screen.findByTestId("top-bar-repo-drawer-trigger");
    expect(trigger.className).toContain("sm:hidden");
    await user.click(trigger);
    expect(await screen.findByTestId("repo-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("repo-drawer-switcher")).toBeInTheDocument();
  });

  it("defaults the drawer's engine key to the first connected repo when nothing is scoped yet", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    mockConversations();
    const user = userEvent.setup();
    renderAt("/health");
    await user.click(await screen.findByTestId("top-bar-repo-drawer-trigger"));
    expect(await screen.findByTestId("conversation-rail-acme/widget")).toBeInTheDocument();
  });

  it("closing the drawer's close button hides it again", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    mockConversations();
    const user = userEvent.setup();
    renderAt("/health");
    await user.click(await screen.findByTestId("top-bar-repo-drawer-trigger"));
    await user.click(await screen.findByTestId("repo-drawer-close"));
    expect(screen.queryByTestId("repo-drawer")).not.toBeInTheDocument();
  });

  it("renders no compact trigger with nothing connected", async () => {
    mockEngines([]);
    renderAt("/health");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId("top-bar-repo-drawer-trigger")).not.toBeInTheDocument();
  });

  it("scopes the drawer to the mirrored repository when one is already selected", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    mockConversations();
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/widget");
    const user = userEvent.setup();
    renderAt("/health");
    await user.click(await screen.findByTestId("top-bar-repo-drawer-trigger"));
    expect(await screen.findByTestId("conversation-rail-acme/widget")).toBeInTheDocument();
  });
});

const AARDVARK_KEY = "acme/aardvark";
const OTHER_REPO_KEYS = [AARDVARK_KEY, STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY];

const EXPECTED_SWITCHER_ORDER: Record<DefaultKeyState, string[]> = {
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
  );
}

function ListingProbe() {
  const { loaded } = useEngines();
  return <span data-testid="listing-loaded">{String(loaded)}</span>;
}

async function renderAfterListing(initial: string): Promise<void> {
  render(
    <MemoryRouter initialEntries={[initial]}>
      <ListingProbe />
      <Routes>
        <Route path="*" element={<TopBarRepoSwitcher />} />
      </Routes>
    </MemoryRouter>,
  );
  await waitFor(() => {
    expect(screen.getByTestId("listing-loaded")).toHaveTextContent("true");
  });
}

function switcherOrder(): (string | undefined)[] {
  const nav = screen.getByTestId("top-bar-repo-switcher");
  return within(nav)
    .getAllByRole("link")
    .map((link) => link.dataset.testid?.replace("top-bar-repo-", ""));
}

describe("TopBarRepoSwitcher — listed default repository", () => {
  it.each(DEFAULT_KEY_STATES)(
    "sorts the resolved default repository first and the rest by title (%s)",
    async (state, defaultKey) => {
      stubListing(OTHER_REPO_KEYS, defaultKey);
      await renderAfterListing("/health");
      expect(switcherOrder()).toEqual(EXPECTED_SWITCHER_ORDER[state]);
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "renders no switcher when the only registered repository is an unconfigured resolved default (%s)",
    async (state, defaultKey) => {
      stubListing([RESOLVED_DEFAULT[state]], defaultKey);
      await renderAfterListing("/health");
      expect(screen.queryByTestId("top-bar-repo-switcher")).not.toBeInTheDocument();
      expect(screen.queryByTestId("top-bar-repo-drawer-trigger")).not.toBeInTheDocument();
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "lists exactly the lone configured default, current on its dashboard and not current elsewhere (%s)",
    async (state, defaultKey) => {
      const own = RESOLVED_DEFAULT[state];
      stubListing([own], defaultKey, makeConfiguredEngineSummaryFor);
      await renderAfterListing(`/repos/${own}`);
      const entry = await screen.findByTestId(`top-bar-repo-${own}`);
      expect(switcherOrder()).toEqual([own]);
      expect(entry).toHaveAttribute("href", `/repos/${own}`);
      expect(entry).toHaveAttribute("aria-current", "true");
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "marks the lone configured default as current on no page but its own dashboard (%s)",
    async (state, defaultKey) => {
      const own = RESOLVED_DEFAULT[state];
      stubListing([own], defaultKey, makeConfiguredEngineSummaryFor);
      await renderAfterListing("/health");
      const entry = await screen.findByTestId(`top-bar-repo-${own}`);
      expect(switcherOrder()).toEqual([own]);
      expect(entry).not.toHaveAttribute("aria-current");
    },
  );

  it("makes the lone configured default's entry keyboard-focusable", async () => {
    stubListing([LISTED_DEFAULT_KEY], LISTED_DEFAULT_KEY, makeConfiguredEngineSummaryFor);
    await renderAfterListing("/health");
    const entry = await screen.findByTestId(`top-bar-repo-${LISTED_DEFAULT_KEY}`);
    entry.focus();
    expect(entry).toHaveFocus();
    expect(entry.className).toContain("focus-visible:ring-2");
  });

  it("renders the compact drawer trigger for a lone configured default off the repository shell", async () => {
    stubListing([LISTED_DEFAULT_KEY], LISTED_DEFAULT_KEY, makeConfiguredEngineSummaryFor);
    await renderAfterListing("/health");
    const trigger = await screen.findByTestId("top-bar-repo-drawer-trigger");
    expect(trigger.className).toContain("sm:hidden");
  });

  it.each(DEFAULT_KEY_STATES)(
    "renders a switcher for a lone built-in repository only when the listed default differs from it (%s)",
    async (state, defaultKey) => {
      stubListing([STUB_DEFAULT_ENGINE_KEY], defaultKey);
      await renderAfterListing("/health");
      expect(screen.queryByTestId("top-bar-repo-switcher") !== null).toBe(
        LONE_BUILT_IN_SHOWS_SWITCHER[state],
      );
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "marks the resolved default repository for a workspace with no engine key (%s)",
    async (state, defaultKey) => {
      stubListing(OTHER_REPO_KEYS, defaultKey);
      server.use(
        http.get("/api/workspaces/t1", () =>
          HttpResponse.json(makeWorkspaceDetail({ engine_key: null })),
        ),
      );
      await renderAfterListing("/workspaces/t1");
      const selected = await screen.findByTestId(`top-bar-repo-${RESOLVED_DEFAULT[state]}`);
      await waitFor(() => {
        expect(selected).toHaveAttribute("aria-current", "true");
      });
      const marked = within(screen.getByTestId("top-bar-repo-switcher"))
        .getAllByRole("link")
        .filter((link) => link.hasAttribute("aria-current"));
      expect(marked).toEqual([selected]);
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "scopes the drawer to the first entry, the resolved default, when nothing is scoped (%s)",
    async (state, defaultKey) => {
      stubListing(OTHER_REPO_KEYS, defaultKey);
      mockConversations();
      const user = userEvent.setup();
      await renderAfterListing("/health");
      await user.click(await screen.findByTestId("top-bar-repo-drawer-trigger"));
      expect(
        await screen.findByTestId(`conversation-rail-${RESOLVED_DEFAULT[state]}`),
      ).toBeInTheDocument();
    },
  );
});

describe("TopBarRepoSwitcher — no listed default", () => {
  it("lists every registered repository by title, since none is the default", async () => {
    stubListing([LISTED_DEFAULT_KEY, AARDVARK_KEY], undefined);
    await renderAfterListing("/health");
    expect(switcherOrder()).toEqual([AARDVARK_KEY, LISTED_DEFAULT_KEY]);
  });

  it("lists a lone unconfigured repository, since it cannot be the default", async () => {
    stubListing([AARDVARK_KEY], undefined);
    await renderAfterListing("/health");
    expect(switcherOrder()).toEqual([AARDVARK_KEY]);
  });

  it("marks no repository for a workspace with no engine key", async () => {
    stubListing([LISTED_DEFAULT_KEY, AARDVARK_KEY], undefined);
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(makeWorkspaceDetail({ engine_key: null }))),
    );
    await renderAfterListing("/workspaces/t1");
    await screen.findByTestId("top-bar-repo-switcher");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(
      within(screen.getByTestId("top-bar-repo-switcher"))
        .getAllByRole("link")
        .filter((link) => link.hasAttribute("aria-current")),
    ).toEqual([]);
  });

  it("renders no switcher and no drawer trigger with nothing registered", async () => {
    stubListing([], undefined);
    await renderAfterListing("/health");
    expect(screen.queryByTestId("top-bar-repo-switcher")).not.toBeInTheDocument();
    expect(screen.queryByTestId("top-bar-repo-drawer-trigger")).not.toBeInTheDocument();
    expect(screen.getByTestId("connect-repo-cta")).toBeInTheDocument();
  });
});
