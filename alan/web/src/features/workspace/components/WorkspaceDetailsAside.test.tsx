import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEngines } from "../../../hooks/useEngines";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  makeEngineSummaryFor,
  settleDefaultEngineKey,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import WorkspaceDetailsAside from "./WorkspaceDetailsAside";

beforeEach(() => {
  server.use(
    http.get(/\/api\/workspaces\/[^/]+\/dispatch-instructions$/, () => HttpResponse.json({})),
    http.get(/\/api\/workspaces\/[^/]+\/sticky-instructions$/, () =>
      HttpResponse.json({ text: null, origin: null }),
    ),
  );
});

function renderAside(ws: ReturnType<typeof makeWorkspaceDetail>) {
  return render(
    <MemoryRouter>
      <WorkspaceDetailsAside ws={ws} taskId="t1" onInstructionCleared={vi.fn()} />
    </MemoryRouter>,
  );
}

function stubNarrow() {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: false,
    media: "",
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  });
}

class TogglingMediaQueryList {
  matches: boolean;
  readonly media: string;
  private listeners: ((event: MediaQueryListEvent) => void)[] = [];

  constructor(media: string, matches: boolean) {
    this.media = media;
    this.matches = matches;
  }

  addEventListener(_type: "change", listener: (event: MediaQueryListEvent) => void): void {
    this.listeners.push(listener);
  }

  removeEventListener(_type: "change", listener: (event: MediaQueryListEvent) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener);
  }

  emit(matches: boolean): void {
    this.matches = matches;
    for (const listener of this.listeners) {
      listener({ matches } as MediaQueryListEvent);
    }
  }
}

function stubTogglingMediaQuery(initialMatches: boolean): TogglingMediaQueryList {
  const mql = new TogglingMediaQueryList("(min-width: 640px)", initialMatches);
  vi.spyOn(window, "matchMedia").mockReturnValue(mql as unknown as MediaQueryList);
  return mql;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WorkspaceDetailsAside — wrapper", () => {
  it("renders as an <aside> on wide screens", () => {
    renderAside(makeWorkspaceDetail());
    expect(screen.getByTestId("workspace-details-aside").tagName).toBe("ASIDE");
    expect(screen.queryByTestId("workspace-details-disclosure")).not.toBeInTheDocument();
  });

  it("renders as a closed 'Details' disclosure on narrow screens", () => {
    stubNarrow();
    renderAside(makeWorkspaceDetail());
    const disclosure = screen.getByTestId("workspace-details-disclosure");
    expect(disclosure).not.toHaveAttribute("open", "");
    expect(screen.getByText("Details")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace-details-aside")).not.toBeInTheDocument();
  });

  it("mounts the fact content exactly once regardless of breakpoint", () => {
    renderAside(makeWorkspaceDetail({ engine_key: "acme/sprocket" }));
    expect(screen.getAllByTestId("workspace-repository")).toHaveLength(1);
  });

  it("keeps unsaved note text when the viewport crosses the breakpoint", async () => {
    const user = userEvent.setup();
    const mql = stubTogglingMediaQuery(true);
    renderAside(makeWorkspaceDetail());

    const noteInput = screen.getByRole("textbox", { name: "Personal note" });
    await user.type(noteInput, "draft in progress");
    expect(noteInput).toHaveValue("draft in progress");

    act(() => {
      mql.emit(false);
    });

    expect(screen.getByTestId("workspace-details-disclosure")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Personal note" })).toHaveValue(
      "draft in progress",
    );
  });
});

describe("WorkspaceDetailsAside — facts", () => {
  it("shows the repository, falling back to the default engine slug", () => {
    settleDefaultEngineKey(STUB_DEFAULT_ENGINE_KEY);
    renderAside(makeWorkspaceDetail({ engine_key: null }));
    expect(screen.getByTestId("workspace-repository")).toHaveTextContent(STUB_DEFAULT_ENGINE_KEY);
  });

  it("shows no repository fact when the workspace names none and no default is known", () => {
    renderAside(makeWorkspaceDetail({ engine_key: null }));
    expect(screen.queryByTestId("workspace-repository")).not.toBeInTheDocument();
  });

  it("shows the workspace's own engine_key when present", () => {
    renderAside(makeWorkspaceDetail({ engine_key: "acme/sprocket" }));
    expect(screen.getByTestId("workspace-repository")).toHaveTextContent("acme/sprocket");
  });

  it("shows the created date", () => {
    renderAside(makeWorkspaceDetail({ created_at: "2026-05-12T08:00:00Z" }));
    expect(screen.getByTestId("workspace-created")).toBeInTheDocument();
  });

  it("shows an empty-state dash for elapsed time when active_seconds is null", () => {
    renderAside(makeWorkspaceDetail({ active_seconds: null }));
    expect(screen.getByTestId("workspace-elapsed")).toHaveTextContent("—");
  });

  it("shows the total cost with two decimals", () => {
    renderAside(makeWorkspaceDetail({ total_cost_usd: 1.5 }));
    expect(screen.getByTestId("workspace-total-cost")).toHaveTextContent("Total: $1.50");
  });

  it("omits the project link when project_id is absent", () => {
    renderAside(makeWorkspaceDetail({ project_id: null }));
    expect(screen.queryByTestId("project-link")).not.toBeInTheDocument();
  });

  it("shows a project link to the project page when project_id is present", () => {
    renderAside(makeWorkspaceDetail({ project_id: "proj-1" }));
    expect(screen.getByTestId("project-link")).toHaveAttribute("href", "/projects/proj-1");
  });

  it("mounts the note editor and sticky-instruction card", () => {
    renderAside(makeWorkspaceDetail());
    expect(screen.getByTestId("note-editor")).toBeInTheDocument();
  });
});

function ListingProbe() {
  const { loaded } = useEngines();
  return <span data-testid="listing-loaded">{String(loaded)}</span>;
}

async function renderAsideAfterListing(
  ws: ReturnType<typeof makeWorkspaceDetail>,
  defaultKey: string | undefined,
): Promise<void> {
  server.use(
    engineListingHandler({
      engines: [makeEngineSummaryFor("acme/sprocket")],
      defaultKey,
    }),
  );
  render(
    <MemoryRouter>
      <ListingProbe />
      <WorkspaceDetailsAside ws={ws} taskId="t1" onInstructionCleared={vi.fn()} />
    </MemoryRouter>,
  );
  await waitFor(() => {
    expect(screen.getByTestId("listing-loaded")).toHaveTextContent("true");
  });
}

describe("WorkspaceDetailsAside — repository fact against the listed default", () => {
  it.each(DEFAULT_KEY_STATES)(
    "shows the resolved default repository when the workspace has no engine_key (%s)",
    async (_state, defaultKey) => {
      await renderAsideAfterListing(makeWorkspaceDetail({ engine_key: null }), defaultKey);
      expect(screen.getByTestId("workspace-repository")).toHaveTextContent(
        defaultKey,
      );
    },
  );

  it("shows no repository fact for a workspace with no engine_key once the list names no default", async () => {
    await renderAsideAfterListing(makeWorkspaceDetail({ engine_key: null }), undefined);
    expect(screen.queryByTestId("workspace-repository")).not.toBeInTheDocument();
  });

  it.each(DEFAULT_KEY_STATES)(
    "shows the workspace's own engine_key regardless of the listed default (%s)",
    async (_state, defaultKey) => {
      await renderAsideAfterListing(makeWorkspaceDetail({ engine_key: "acme/sprocket" }), defaultKey);
      expect(screen.getByTestId("workspace-repository")).toHaveTextContent("acme/sprocket");
    },
  );
});
