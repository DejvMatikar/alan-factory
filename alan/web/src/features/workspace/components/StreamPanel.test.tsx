import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { StreamEvent } from "../../../api";
import {
  activateDisclosure,
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { server } from "../../../test/server";
import StreamPanel, { type TaggedEvent } from "./StreamPanel";

const TASK = "t1";
const INV = "inv_001";
const ROLE = "planner";
const STREAM_URL = `/api/workspaces/${TASK}/invocations/${INV}/agents/${ROLE}/stream`;

function stubStream(events: StreamEvent[] = []) {
  server.use(http.get(STREAM_URL, () => HttpResponse.json(events)));
}

function stubStreamHang() {
  server.use(
    http.get(STREAM_URL, () => new Promise<Response>(() => undefined)),
  );
}

function stubStreamError() {
  server.use(
    http.get(STREAM_URL, () =>
      HttpResponse.json({ detail: "not found" }, { status: 404 }),
    ),
  );
}

function renderPanel(
  overrides: Partial<{
    nodeStatus: "pending" | "running" | "success" | "failed" | "interrupted";
    liveEvents: readonly TaggedEvent[];
    nodeError: string | null;
  }> = {},
) {
  return render(
    <StreamPanel
      taskId={TASK}
      invId={INV}
      role={ROLE}
      liveEvents={overrides.liveEvents ?? []}
      {...(overrides.nodeStatus !== undefined && {
        nodeStatus: overrides.nodeStatus,
      })}
      nodeError={overrides.nodeError ?? null}
    />,
  );
}

const SYS_INIT: StreamEvent = { type: "system", subtype: "init" };
const TOOL_READ: StreamEvent = {
  type: "assistant",
  message: {
    content: [
      { type: "tool_use", name: "Read", input: { file_path: "foo.ts" } },
    ],
  },
};
const RESULT_OK: StreamEvent = {
  type: "result",
  subtype: "success",
  result: "done",
};

describe("StreamPanel", () => {
  it("shows loading indicator while the API call is in flight", () => {
    stubStreamHang();
    renderPanel({ nodeStatus: "running" });
    expect(screen.getByText("loading…")).toBeInTheDocument();
  });

  it("hides loading indicator after API returns", async () => {
    stubStream([]);
    renderPanel({ nodeStatus: "running" });
    await waitFor(() => {
      expect(screen.queryByText("loading…")).not.toBeInTheDocument();
    });
  });

  it("shows stream error when API fails", async () => {
    stubStreamError();
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stream-error");
    expect(screen.getByTestId("stream-error")).toHaveTextContent("404");
  });

  it("shows 'No events yet.' when expanded with zero events", async () => {
    stubStream([]);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stream-empty");
  });

  it("does NOT show 'No events yet.' when collapsed with zero events", async () => {
    stubStream([]);
    renderPanel({ nodeStatus: "success" });
    await waitFor(() => {
      expect(screen.queryByText("loading…")).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId("stream-empty")).not.toBeInTheDocument();
  });

  it("bounds the event list's height so a long run cannot grow the page unbounded", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    const list = await screen.findByTestId("stream-event-list");
    expect(list.className).toContain("max-h-");
    expect(list.className).toContain("overflow-y-auto");
  });

  it("renders StreamRow summary instead of raw JSON", async () => {
    stubStream([SYS_INIT, TOOL_READ, RESULT_OK]);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stream-row-system");
    expect(screen.getByTestId("stream-row-system")).toHaveTextContent("Init");
    expect(screen.getByTestId("stream-row-assistant")).toHaveTextContent(
      "Read: foo.ts",
    );
    expect(screen.getByTestId("stream-row-result")).toHaveTextContent(
      "Done: done",
    );
  });

  it("wraps each row in a <details> disclosure element", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stream-row-system");
    const li = screen.getByTestId("stream-row-system");
    expect(li.querySelector("details")).not.toBeNull();
    expect(li.querySelector("summary")).not.toBeNull();
  });

  it("starts each row's JSON detail closed with ▸ beside the summary text", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    const li = await screen.findByTestId("stream-row-system");
    const details = li.querySelector<HTMLElement>("details")!;
    await expectDisclosureState(details, false);
    expect(disclosureSummary(details)).toHaveTextContent("Init");
    expect(details.querySelector("pre")).not.toBeVisible();
  });

  it("opens and closes a row's JSON detail on click, Enter and Space, revealing the raw event while open", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    const li = await screen.findByTestId("stream-row-system");
    const details = li.querySelector<HTMLElement>("details")!;
    await expectTogglesOnEveryActivation(details);
    activateDisclosure(details, "Enter");
    await expectDisclosureState(details, true);
    expect(details.querySelector("pre")).toBeVisible();
    expect(details.querySelector("pre")).toHaveTextContent('"subtype": "init"');
  });

  it("gives the stats toggle an accessible name, its open state and a purpose description", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    const toggle = await screen.findByTestId("stream-stats-toggle");
    expect(toggle).toHaveAccessibleName("Collapse events");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAccessibleDescription("Hide this step's events");

    await userEvent.click(toggle);

    expect(toggle).toHaveAccessibleName("Expand events");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAccessibleDescription("Show this step's events");
  });

  it("shows the stats toggle's description on keyboard focus and meets the 44px target", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    const toggle = await screen.findByTestId("stream-stats-toggle");
    expect(toggle).toHaveAttribute("data-icon-only", "true");
    expect(toggle.className).toContain("min-h-11");
    expect(toggle.className).toContain("min-w-11");
    const description = document.getElementById(toggle.getAttribute("aria-describedby") ?? "");
    expect(description).toHaveClass("sr-only");

    act(() => {
      toggle.focus();
    });

    expect(toggle).toHaveFocus();
    expect(description).not.toHaveClass("sr-only");
  });

  it("toggles the event list from the keyboard alone", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    const toggle = await screen.findByTestId("stream-stats-toggle");
    toggle.focus();

    await userEvent.keyboard("{Enter}");
    expect(screen.queryByTestId("stream-row-system")).not.toBeInTheDocument();

    await userEvent.keyboard(" ");
    expect(screen.getByTestId("stream-row-system")).toBeInTheDocument();
  });

  it("auto-collapses the event list when nodeStatus is 'success'", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "success" });
    await waitFor(() => {
      expect(screen.queryByText("loading…")).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId("stream-row-system")).not.toBeInTheDocument();
    expect(screen.getByTestId("stream-stats-bar")).toBeInTheDocument();
  });

  it("auto-expands the event list when nodeStatus is 'running'", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stream-row-system");
  });

  it("auto-expands the event list when nodeStatus is 'pending'", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "pending" });
    await screen.findByTestId("stream-row-system");
  });

  it("toggling the stats-bar button collapses then re-expands the event list", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stream-row-system");

    await userEvent.click(screen.getByTestId("stream-stats-toggle"));
    await waitFor(() => {
      expect(screen.queryByTestId("stream-row-system")).not.toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("stream-stats-toggle"));
    await screen.findByTestId("stream-row-system");
  });

  it("stats bar is absent when there are no events", async () => {
    stubStream([]);
    renderPanel({ nodeStatus: "running" });
    await waitFor(() => {
      expect(screen.queryByText("loading…")).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId("stream-stats-bar")).not.toBeInTheDocument();
  });

  it("stats bar shows 'Currently:' when nodeStatus is 'running'", async () => {
    stubStream([SYS_INIT, TOOL_READ, RESULT_OK]);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stats-current-action");
    expect(screen.getByTestId("stats-current-action")).toHaveTextContent(
      "Currently:",
    );
  });

  it("stats bar shows 'Currently:' when nodeStatus is 'pending'", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "pending" });
    await screen.findByTestId("stats-current-action");
    expect(screen.getByTestId("stats-current-action")).toHaveTextContent(
      "Currently:",
    );
  });

  it("stats bar shows 'Last:' when nodeStatus is 'success'", async () => {
    stubStream([SYS_INIT, TOOL_READ, RESULT_OK]);
    renderPanel({ nodeStatus: "success" });
    await screen.findByTestId("stats-current-action");
    expect(screen.getByTestId("stats-current-action")).toHaveTextContent(
      "Last:",
    );
  });

  it("stats bar shows tool counts", async () => {
    stubStream([SYS_INIT, TOOL_READ, RESULT_OK]);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stats-tool-counts");
    expect(screen.getByTestId("stats-tool-counts")).toHaveTextContent(
      "Read ×1",
    );
  });

  it("stats bar shows totalOther when more than 3 distinct tools used", async () => {
    const events: StreamEvent[] = [
      SYS_INIT,
      {
        type: "assistant",
        message: { content: [{ type: "tool_use", name: "Read" }] },
      },
      {
        type: "assistant",
        message: { content: [{ type: "tool_use", name: "Write" }] },
      },
      {
        type: "assistant",
        message: { content: [{ type: "tool_use", name: "Bash" }] },
      },
      {
        type: "assistant",
        message: { content: [{ type: "tool_use", name: "Grep" }] },
      },
      RESULT_OK,
    ];
    stubStream(events);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stats-tool-counts");
    expect(screen.getByTestId("stats-tool-counts")).toHaveTextContent("other");
  });

  it("stats bar omits current-action when all events are result type", async () => {
    stubStream([RESULT_OK]);
    renderPanel({ nodeStatus: "success" });
    await screen.findByTestId("stream-stats-bar");
    expect(
      screen.queryByTestId("stats-current-action"),
    ).not.toBeInTheDocument();
  });

  it("stats bar omits tool-counts when no tool_use events", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "running" });
    await screen.findByTestId("stream-stats-bar");
    expect(screen.queryByTestId("stats-tool-counts")).not.toBeInTheDocument();
  });

  it("toggle button shows ▸ when collapsed and ▾ when expanded", async () => {
    stubStream([SYS_INIT]);
    renderPanel({ nodeStatus: "success" });
    await screen.findByTestId("stream-stats-toggle");
    expect(screen.getByTestId("stream-stats-toggle")).toHaveTextContent("▸");

    await userEvent.click(screen.getByTestId("stream-stats-toggle"));
    expect(screen.getByTestId("stream-stats-toggle")).toHaveTextContent("▾");
  });

  it("renders NodeErrorFooter when nodeError is provided", async () => {
    stubStream([]);
    renderPanel({ nodeStatus: "failed", nodeError: "timeout after 60s" });
    await waitFor(() => {
      expect(screen.queryByText("loading…")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("stream-node-error")).toHaveTextContent(
      "timeout after 60s",
    );
  });

  it("does not render NodeErrorFooter when nodeError is null", async () => {
    stubStream([]);
    renderPanel({ nodeStatus: "success" });
    await waitFor(() => {
      expect(screen.queryByText("loading…")).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId("stream-node-error")).not.toBeInTheDocument();
  });

  it("ignores fetch resolution after unmount (cancelled guard in .then)", async () => {
    const holder: { fn: ((events: StreamEvent[]) => void) | null } = {
      fn: null,
    };
    server.use(
      http.get(STREAM_URL, () =>
        new Promise<Response>((resolve) => {
          holder.fn = (events) => resolve(HttpResponse.json(events));
        }),
      ),
    );
    const { unmount } = renderPanel({ nodeStatus: "running" });
    await screen.findByText("loading…");
    unmount();
    holder.fn?.([]);
    await new Promise((r) => setTimeout(r, 10));
  });

  it("ignores fetch rejection after unmount (cancelled guard in .catch)", async () => {
    const holder: { fn: (() => void) | null } = { fn: null };
    server.use(
      http.get(STREAM_URL, () =>
        new Promise<Response>((resolve) => {
          holder.fn = () => resolve(HttpResponse.error());
        }),
      ),
    );
    const { unmount } = renderPanel({ nodeStatus: "running" });
    await screen.findByText("loading…");
    unmount();
    holder.fn?.();
    await new Promise((r) => setTimeout(r, 10));
  });
});
