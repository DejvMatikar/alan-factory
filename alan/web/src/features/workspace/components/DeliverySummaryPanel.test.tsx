import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type Handover } from "../../../api";
import { makeHandover } from "../../../test/workspaceFixtures";
import DeliverySummaryPanel from "./DeliverySummaryPanel";

function retroHandover(fromTeam: string, synthesis: string): Handover {
  return makeHandover({
    from_team: fromTeam,
    full_synthesis: synthesis,
    timestamp_utc: "2026-05-20T10:00:00Z",
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DeliverySummaryPanel", () => {
  it("renders null when status is not completed", () => {
    render(<DeliverySummaryPanel taskId="t1" status="archived" liveAt={null} />);
    expect(screen.queryByTestId("delivery-summary-panel")).toBeNull();
  });

  it("renders loading state immediately on mount", () => {
    vi.spyOn(api, "getHandovers").mockReturnValue(new Promise<Handover[]>(() => undefined));
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    expect(screen.getByTestId("delivery-summary-panel")).toBeInTheDocument();
    expect(screen.getByTestId("delivery-loading")).toBeInTheDocument();
  });

  it("renders a 'Live since' line from liveAt immediately, before handovers load", () => {
    vi.spyOn(api, "getHandovers").mockReturnValue(new Promise<Handover[]>(() => undefined));
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt="2026-05-21T09:30:00Z" />);
    expect(screen.getByTestId("live-since")).toHaveTextContent("Live since");
    expect(screen.getByRole("time")).toHaveAttribute("dateTime", "2026-05-21T09:30:00Z");
  });

  it("omits the 'Live since' line entirely when liveAt is null but retrospective content exists", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([
      retroHandover("retrospective", "## What worked\nit went well"),
    ]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await screen.findByTestId("retrospective");
    expect(screen.queryByTestId("live-since")).toBeNull();
  });

  it("renders nothing once handovers load with no live-since line, no retrospective and no deferred items", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await waitFor(() => {
      expect(screen.queryByTestId("delivery-loading")).toBeNull();
    });
    expect(screen.queryByTestId("delivery-summary-panel")).toBeNull();
  });

  it("renders error state when api.getHandovers rejects with an Error", async () => {
    vi.spyOn(api, "getHandovers").mockRejectedValue(new Error("fetch failed"));
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    expect(await screen.findByTestId("delivery-error")).toHaveTextContent(
      "Failed to load retrospective: fetch failed",
    );
  });

  it("stringifies non-Error rejection into the error state", async () => {
    vi.spyOn(api, "getHandovers").mockRejectedValue("plain-string-error");
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    expect(await screen.findByTestId("delivery-error")).toHaveTextContent(
      "Failed to load retrospective: plain-string-error",
    );
  });

  it("does not render any PR link, changed-files or commits content", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([
      retroHandover("retrospective", "## What worked\nit went well"),
    ]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await screen.findByTestId("delivery-summary-panel");
    expect(screen.queryByTestId("what-shipped")).toBeNull();
    expect(screen.queryByTestId("commits")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("renders as a flat section with no card chrome", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([
      retroHandover("retrospective", "## What worked\nit went well"),
    ]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    const section = await screen.findByTestId("delivery-summary-panel");
    expect(section.className).not.toContain("rounded-lg");
    expect(section.className).not.toContain("border");
    expect(section.className).not.toContain("bg-surface-elevated");
  });

  it("resets to loading state when taskId changes", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([
      retroHandover("retrospective", "## What worked\nit went well"),
    ]);
    const { rerender } = render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await screen.findByTestId("retrospective");

    vi.spyOn(api, "getHandovers").mockReturnValue(new Promise<Handover[]>(() => undefined));
    rerender(<DeliverySummaryPanel taskId="t2" status="completed" liveAt={null} />);
    expect(screen.getByTestId("delivery-loading")).toBeInTheDocument();
  });

  it("ignores resolved handovers after unmount (ignore flag — then branch)", async () => {
    let resolveHandovers!: (v: Handover[]) => void;
    vi.spyOn(api, "getHandovers").mockReturnValue(
      new Promise((res) => {
        resolveHandovers = res;
      }),
    );
    const { unmount } = render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    expect(screen.getByTestId("delivery-loading")).toBeInTheDocument();
    unmount();
    resolveHandovers([]);
    await new Promise((r) => setTimeout(r, 10));
  });

  it("ignores rejected handovers after unmount (ignore flag — catch branch)", async () => {
    let rejectHandovers!: (reason: unknown) => void;
    vi.spyOn(api, "getHandovers").mockReturnValue(
      new Promise((_res, rej) => {
        rejectHandovers = rej;
      }),
    );
    const { unmount } = render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    expect(screen.getByTestId("delivery-loading")).toBeInTheDocument();
    unmount();
    rejectHandovers(new Error("late error"));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("retrospective section absent when no handover has from_team retrospective", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([retroHandover("pr_merge", "## What worked\nfoo")]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt="2026-05-20T10:00:00Z" />);
    await screen.findByTestId("delivery-summary-panel");
    await waitFor(() => {
      expect(screen.queryByTestId("delivery-loading")).toBeNull();
    });
    expect(screen.queryByTestId("retrospective")).toBeNull();
  });

  it("retrospective section absent when synthesis lacks all three retro sections", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([
      retroHandover("retrospective", "## Action items\nfoo"),
    ]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await screen.findByTestId("deferred");
    expect(screen.queryByTestId("retrospective")).toBeNull();
  });

  it("retrospective renders only sections present in the synthesis, under an h3 heading", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([
      retroHandover("retrospective", "## What worked\nIt went well\n## Action items\nfoo"),
    ]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await screen.findByTestId("retrospective");
    expect(screen.getByRole("heading", { level: 3, name: "Retrospective" })).toBeInTheDocument();
    expect(screen.getByText("What worked")).toBeInTheDocument();
    expect(screen.queryByText("What didn't")).toBeNull();
    expect(screen.queryByText("How to verify in the UI")).toBeNull();
  });

  it("retrospective renders all three sub-blocks when synthesis has all three sections", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([
      retroHandover(
        "retrospective",
        "## What worked\nw\n## What didn't\nd\n## How to verify in the UI\nv",
      ),
    ]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await screen.findByTestId("retrospective");
    expect(screen.getByText("What worked")).toBeInTheDocument();
    expect(screen.getByText("What didn't")).toBeInTheDocument();
    expect(screen.getByText("How to verify in the UI")).toBeInTheDocument();
  });

  it("deferred section absent when synthesis has no Action items section", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([retroHandover("retrospective", "## What worked\nfoo")]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await screen.findByTestId("retrospective");
    expect(screen.queryByTestId("deferred")).toBeNull();
  });

  it("deferred renders one li per non-empty line, skipping whitespace-only lines, under an h3 heading", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([
      retroHandover("retrospective", "## Action items\nitem one\n   \nitem two"),
    ]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt={null} />);
    await screen.findByTestId("deferred");
    expect(screen.getByRole("heading", { level: 3, name: "Deferred & follow-ups" })).toBeInTheDocument();
    expect(screen.getByTestId("deferred-item-0")).toHaveTextContent("item one");
    expect(screen.getByTestId("deferred-item-1")).toHaveTextContent("item two");
    expect(screen.queryByTestId("deferred-item-2")).toBeNull();
  });

  it("deferred returns null when all action-item lines are empty after bullet stripping", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([retroHandover("retrospective", "## Action items\n- \n* ")]);
    render(<DeliverySummaryPanel taskId="t1" status="completed" liveAt="2026-05-20T10:00:00Z" />);
    await screen.findByTestId("delivery-summary-panel");
    await waitFor(() => {
      expect(screen.queryByTestId("delivery-loading")).toBeNull();
    });
    expect(screen.queryByTestId("deferred")).toBeNull();
  });
});
