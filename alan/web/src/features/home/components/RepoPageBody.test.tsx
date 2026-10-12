import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import RepoPageBody, { type RepoPageBodyProps } from "./RepoPageBody";
import type { RepoHeaderModel, RepoSectionModel } from "../../../lib/fold";

function emptySection(overrides: Partial<RepoSectionModel> = {}): RepoSectionModel {
  return {
    key: "acme/widget",
    title: "Widget",
    posture: "autonomous",
    projectGroups: [],
    unassigned: [],
    shippedCount: 0,
    archivedCount: 0,
    ...overrides,
  };
}

function header(overrides: Partial<RepoHeaderModel> = {}): RepoHeaderModel {
  return {
    key: "acme/widget",
    title: "Widget",
    posture: "autonomous",
    ...overrides,
  };
}

const NOOP_PROPS: RepoPageBodyProps = {
  tab: "active",
  onSelectTab: vi.fn(),
  loadError: null,
  ready: true,
  header: header(),
  section: emptySection(),
  reload: vi.fn(),
  showProjects: true,
  resolvedAccent: null,
  postureResolved: true,
  hostSetupMessage: null,
  resumes: [],
};

function renderBody(overrides: Partial<RepoPageBodyProps> = {}) {
  return render(
    <MemoryRouter>
      <RepoPageBody {...NOOP_PROPS} {...overrides} />
    </MemoryRouter>,
  );
}

describe("RepoPageBody — toolbar and tabs", () => {
  it("renders the Active/Archived tabs inside the repository section, directly above the workspace table", () => {
    renderBody();
    expect(screen.getByRole("tab", { name: "Active" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Archived" })).toHaveAttribute("aria-selected", "false");
    const tablist = screen.getByRole("tablist");
    const section = screen.getByTestId(`repo-section-${NOOP_PROPS.section.key}`);
    expect(section).toContainElement(tablist);
    const empty = screen.getByTestId(`repo-empty-${NOOP_PROPS.section.key}`);
    expect(tablist.compareDocumentPosition(empty) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders the tabs even while not ready — only the table region enters loading", () => {
    renderBody({ ready: false });
    expect(screen.getByRole("tablist")).toBeInTheDocument();
  });

  it("clicking a tab calls onSelectTab", async () => {
    const onSelectTab = vi.fn();
    renderBody({ onSelectTab });
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Archived" }));
    expect(onSelectTab).toHaveBeenCalledWith("archived");
  });

  it("ArrowRight on the tablist selects Archived", () => {
    const onSelectTab = vi.fn();
    renderBody({ onSelectTab });
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onSelectTab).toHaveBeenCalledWith("archived");
  });

  it("ArrowLeft on the tablist selects Active", () => {
    const onSelectTab = vi.fn();
    renderBody({ tab: "archived", onSelectTab });
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(onSelectTab).toHaveBeenCalledWith("active");
  });

  it("gives Active and Archived each a plain-language description, wrapped ARIA-transparently so the tablist stays valid", () => {
    renderBody();
    expect(screen.getByText("Shows workspaces currently in progress.")).toBeInTheDocument();
    expect(
      screen.getByText("Shows workspaces that have finished or been archived."),
    ).toBeInTheDocument();
    const tab = screen.getByRole("tab", { name: "Active" });
    expect(tab.parentElement).toHaveAttribute("role", "presentation");
  });

  it("a non-arrow key on the tablist is a no-op", () => {
    const onSelectTab = vi.fn();
    renderBody({ onSelectTab });
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Enter" });
    expect(onSelectTab).not.toHaveBeenCalled();
  });

  it("home-repo-sections keeps the same DOM node across a tab change — no key={tab} remount", () => {
    const { rerender } = renderBody({ tab: "active" });
    const before = screen.getByTestId("home-repo-sections");
    rerender(
      <MemoryRouter>
        <RepoPageBody {...NOOP_PROPS} tab="archived" />
      </MemoryRouter>,
    );
    const after = screen.getByTestId("home-repo-sections");
    expect(after).toBe(before);
  });
});

describe("RepoPageBody — loading and error", () => {
  it("shows the loading register before ready, confined to the table region", () => {
    renderBody({ ready: false });
    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  it("hides the loading register once ready", () => {
    renderBody({ ready: true });
    expect(screen.queryByTestId("loading")).not.toBeInTheDocument();
  });

  it("keeps the chrome (heading, tabs) mounted while the table region is loading", () => {
    renderBody({ ready: false });
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  it("renders the load error slot only when there is a load error", () => {
    renderBody({ loadError: "load-boom" });
    expect(screen.getByTestId("home-load-error-slot")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("load-boom");
  });

  it("renders no load-error slot and no alert when there is no load error", () => {
    renderBody({ loadError: null });
    expect(screen.queryByTestId("home-load-error-slot")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stops showing the loading register once a load error lands, even though the fetch never resolved", () => {
    renderBody({ ready: false, loadError: "boom" });
    expect(screen.queryByTestId("loading")).not.toBeInTheDocument();
  });
});

describe("RepoPageBody — needs-you slot", () => {
  it("renders the needs-you slot ahead of the repo sections, with no min-h-11 wrapper", () => {
    renderBody({ hostSetupMessage: "Finish host setup — control token not set" });
    const slot = screen.getByTestId("home-needs-you-slot");
    expect(slot.className).not.toContain("min-h-11");
    const sections = screen.getByTestId("home-repo-sections");
    expect(slot.compareDocumentPosition(sections) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("needs-you-host-setup")).toBeInTheDocument();
  });

  it("renders an empty needs-you slot when there is nothing to show", () => {
    renderBody();
    const slot = screen.getByTestId("home-needs-you-slot");
    expect(slot).toBeEmptyDOMElement();
  });
});

describe("RepoPageBody — section", () => {
  it("renders the header's title once ready", () => {
    renderBody({ header: header({ title: "Widget" }) });
    expect(screen.getByRole("heading", { level: 1, name: "Widget" })).toBeInTheDocument();
  });

  it("renders the repo section unconditionally, even while not ready", () => {
    renderBody({ ready: false });
    expect(screen.getByTestId(`repo-section-${NOOP_PROPS.section.key}`)).toBeInTheDocument();
  });

  it("renders the header's title while not ready — it no longer depends on the workspace/project fetch", () => {
    renderBody({ ready: false, header: header({ title: "Widget" }) });
    expect(screen.getByRole("heading", { level: 1, name: "Widget" })).toBeInTheDocument();
  });

  it("renders the header's title while not ready even when a load error is present", () => {
    renderBody({ ready: false, loadError: "boom", header: header({ title: "Widget" }) });
    expect(screen.getByRole("heading", { level: 1, name: "Widget" })).toBeInTheDocument();
  });

  it("passes postureResolved through to the repo section unchanged", () => {
    renderBody({ postureResolved: false });
    const maintenance = screen.getByTestId(`repo-action-maintenance-${NOOP_PROPS.section.key}`);
    expect(maintenance).toHaveAttribute("aria-disabled", "true");
    expect(maintenance).toHaveAttribute("aria-busy", "true");
  });

  it("threads reload down to the workspace table region", () => {
    const reload = vi.fn();
    const section = emptySection({
      unassigned: [
        makeWorkspaceSummary({ task_id: "w1", title: "w1", project_id: null }),
      ],
    });
    renderBody({ section, reload });
    expect(screen.getByTestId("workspace-row-action-w1")).toBeInTheDocument();
  });
});
