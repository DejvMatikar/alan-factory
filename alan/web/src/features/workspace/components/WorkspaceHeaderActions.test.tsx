import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import WorkspaceHeaderActions, { type WorkspaceHeaderActionsProps } from "./WorkspaceHeaderActions";

function baseProps(): WorkspaceHeaderActionsProps {
  return {
    stopResumeMode: null,
    stopResumeBusy: false,
    onStopResumeClick: vi.fn(),
    requeueCount: 0,
    isArchived: false,
    toggleBusy: false,
    pendingIsArchived: false,
    onToggleClick: vi.fn(),
    onDeleteClick: vi.fn(),
  };
}

function renderActions(overrides: Partial<WorkspaceHeaderActionsProps> = {}) {
  const props = { ...baseProps(), ...overrides };
  render(<WorkspaceHeaderActions {...props} />);
  return props;
}

describe("WorkspaceHeaderActions — status matrix", () => {
  it("shows no stop/resume/requeue control when stopResumeMode is null", () => {
    renderActions({ stopResumeMode: null });
    expect(screen.queryByTestId("stop-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("resume-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("requeue-button")).not.toBeInTheDocument();
  });

  it("shows only Stop when stopResumeMode is 'stop'", () => {
    renderActions({ stopResumeMode: "stop" });
    expect(screen.getByTestId("stop-button")).toBeInTheDocument();
    expect(screen.queryByTestId("resume-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("requeue-button")).not.toBeInTheDocument();
  });

  it("shows only Resume when stopResumeMode is 'resume'", () => {
    renderActions({ stopResumeMode: "resume" });
    expect(screen.getByTestId("resume-button")).toBeInTheDocument();
    expect(screen.queryByTestId("stop-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("requeue-button")).not.toBeInTheDocument();
  });

  it("shows only Re-queue when stopResumeMode is 'requeue'", () => {
    renderActions({ stopResumeMode: "requeue" });
    expect(screen.getByTestId("requeue-button")).toBeInTheDocument();
    expect(screen.queryByTestId("stop-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("resume-button")).not.toBeInTheDocument();
  });

  it("always shows Archive and Delete regardless of stopResumeMode", () => {
    renderActions({ stopResumeMode: "stop" });
    expect(screen.getByTestId("archive-button")).toBeInTheDocument();
    expect(screen.getByTestId("delete-button")).toBeInTheDocument();
  });
});

describe("WorkspaceHeaderActions — visible labels", () => {
  it("labels Stop, Resume and Re-queue", () => {
    renderActions({ stopResumeMode: "stop" });
    expect(screen.getByTestId("stop-button")).toHaveTextContent("Stop");
    renderActions({ stopResumeMode: "resume" });
    expect(screen.getByTestId("resume-button")).toHaveTextContent("Resume");
    renderActions({ stopResumeMode: "requeue" });
    expect(screen.getByTestId("requeue-button")).toHaveTextContent("Re-queue");
  });

  it("labels Archive when not archived", () => {
    renderActions({ isArchived: false });
    expect(screen.getByTestId("archive-button")).toHaveTextContent("Archive");
  });

  it("labels Unarchive when archived", () => {
    renderActions({ isArchived: true });
    expect(screen.getByTestId("archive-button")).toHaveTextContent("Unarchive");
  });

  it("names Delete accessibly as an icon-only control", () => {
    renderActions();
    expect(screen.getByTestId("delete-button")).toHaveAccessibleName("Delete");
  });
});

describe("WorkspaceHeaderActions — descriptions on focus", () => {
  function focusedDescription(button: HTMLElement): HTMLElement | null {
    act(() => {
      button.focus();
    });
    return document.getElementById(button.getAttribute("aria-describedby") ?? "");
  }

  it("describes Stop's consequence", () => {
    renderActions({ stopResumeMode: "stop" });
    expect(focusedDescription(screen.getByTestId("stop-button"))).toHaveTextContent(
      /Stops the current run immediately/,
    );
  });

  it("describes Resume's consequence", () => {
    renderActions({ stopResumeMode: "resume" });
    expect(focusedDescription(screen.getByTestId("resume-button"))).toHaveTextContent(
      /Starts a new run to continue this workspace/,
    );
  });

  it("describes Re-queue's consequence", () => {
    renderActions({ stopResumeMode: "requeue" });
    expect(focusedDescription(screen.getByTestId("requeue-button"))).toHaveTextContent(
      /Retries the failed run/,
    );
  });

  it("describes Archive's consequence", () => {
    renderActions({ isArchived: false });
    expect(focusedDescription(screen.getByTestId("archive-button"))).toHaveTextContent(
      /Moves this workspace out of the active list/,
    );
  });

  it("describes Unarchive's consequence", () => {
    renderActions({ isArchived: true });
    expect(focusedDescription(screen.getByTestId("archive-button"))).toHaveTextContent(
      /Returns this workspace to the active list/,
    );
  });

  it("describes Delete's consequence", () => {
    renderActions();
    expect(focusedDescription(screen.getByTestId("delete-button"))).toHaveTextContent(
      /Permanently removes this workspace/,
    );
  });
});

describe("WorkspaceHeaderActions — Delete vs Archive", () => {
  it("renders Delete last and marks it destroy, distinct from Archive's ghost variant", () => {
    renderActions({ stopResumeMode: "stop" });
    const group = screen.getByRole("group");
    const children = Array.from(group.children);
    const deleteIndex = children.findIndex((el) => el.contains(screen.getByTestId("delete-button")));
    expect(deleteIndex).toBe(children.length - 1);
    expect(screen.getByTestId("delete-button")).toHaveAttribute("data-variant", "destroy");
    expect(screen.getByTestId("archive-button")).toHaveAttribute("data-variant", "ghost");
  });

  it("renders Delete as a quiet icon-only control, never solid-filled", () => {
    renderActions();
    const deleteButton = screen.getByTestId("delete-button");
    expect(deleteButton).toHaveAttribute("data-icon-only", "true");
    expect(deleteButton.className).not.toContain("bg-btn-destroy ");
    expect(deleteButton.className).toContain("min-h-11 min-w-11");
  });
});

describe("WorkspaceHeaderActions — busy labels", () => {
  it("shows 'Stopping…' and stays focusable with aria-busy", () => {
    renderActions({ stopResumeMode: "stop", stopResumeBusy: true });
    const button = screen.getByTestId("stop-button");
    expect(button).toHaveTextContent("Stopping…");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
  });

  it("shows 'Resuming…' and stays focusable with aria-busy", () => {
    renderActions({ stopResumeMode: "resume", stopResumeBusy: true });
    const button = screen.getByTestId("resume-button");
    expect(button).toHaveTextContent("Resuming…");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
  });

  it("shows 'Re-queuing…' and stays focusable with aria-busy", () => {
    renderActions({ stopResumeMode: "requeue", stopResumeBusy: true });
    const button = screen.getByTestId("requeue-button");
    expect(button).toHaveTextContent("Re-queuing…");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
  });

  it("shows 'Archiving…' and stays focusable with aria-busy", () => {
    renderActions({ isArchived: false, toggleBusy: true, pendingIsArchived: false });
    const button = screen.getByTestId("archive-button");
    expect(button).toHaveTextContent("Archiving…");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
  });

  it("shows 'Unarchiving…' and stays focusable with aria-busy", () => {
    renderActions({ isArchived: true, toggleBusy: true, pendingIsArchived: true });
    const button = screen.getByTestId("archive-button");
    expect(button).toHaveTextContent("Unarchiving…");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
  });
});

describe("WorkspaceHeaderActions — Stop behaviour", () => {
  it("calls the handler directly on click, with no dialog involved", async () => {
    const onStopResumeClick = vi.fn();
    renderActions({ stopResumeMode: "stop", onStopResumeClick });
    await userEvent.setup().click(screen.getByTestId("stop-button"));
    expect(onStopResumeClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("WorkspaceHeaderActions — retried counter", () => {
  it("shows 'retried N×' beside Re-queue when requeue_count > 0", () => {
    renderActions({ stopResumeMode: "requeue", requeueCount: 2 });
    expect(screen.getByTestId("requeue-count")).toHaveTextContent("retried 2×");
  });

  it("shows 'retried N×' beside Resume when requeue_count > 0", () => {
    renderActions({ stopResumeMode: "resume", requeueCount: 1 });
    expect(screen.getByTestId("requeue-count")).toHaveTextContent("retried 1×");
  });

  it("omits the counter beside Stop", () => {
    renderActions({ stopResumeMode: "stop", requeueCount: 3 });
    expect(screen.queryByTestId("requeue-count")).not.toBeInTheDocument();
  });

  it("omits the counter when requeue_count is 0", () => {
    renderActions({ stopResumeMode: "requeue", requeueCount: 0 });
    expect(screen.queryByTestId("requeue-count")).not.toBeInTheDocument();
  });
});

describe("WorkspaceHeaderActions — layout", () => {
  it("renders the action group with flex-wrap", () => {
    renderActions();
    expect(screen.getByRole("group").className).toContain("flex-wrap");
  });
});
