import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { EngineSummary } from "../api";
import { server } from "../test/server";
import HiddenRepoDialog, {
  announceHiddenRepoChange,
  HIDDEN_REPO_DISCLOSURE_SUMMARY_ID,
  HiddenRepoAnnouncementRegion,
} from "./HiddenRepoDialog";

function engine(owner: string, repo: string): EngineSummary {
  return { repo: { owner, repo }, posture: "observe", hidden: true };
}

describe("HiddenRepoDialog — persistent trigger", () => {
  it("renders even when nothing is hidden, carrying a zero count", () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const trigger = screen.getByTestId("hidden-repo-dialog-trigger");
    expect(trigger).toHaveTextContent("Hidden (0)");
    expect(trigger).toHaveAccessibleName("Hidden repositories (0)");
    expect(screen.queryByTestId("hidden-repo-dialog")).not.toBeInTheDocument();
  });

  it("carries the live count in its trigger label", () => {
    render(
      <HiddenRepoDialog
        hiddenEngines={[engine("acme", "widget"), engine("acme", "sprocket")]}
        hiddenCount={2}
      />,
    );
    const trigger = screen.getByTestId("hidden-repo-dialog-trigger");
    expect(trigger).toHaveTextContent("Hidden (2)");
    expect(trigger).toHaveAccessibleName("Hidden repositories (2)");
  });

  it("the trigger carries the stable id a repo page's Hide control focuses after hiding", () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    expect(screen.getByTestId("hidden-repo-dialog-trigger")).toHaveAttribute(
      "id",
      HIDDEN_REPO_DISCLOSURE_SUMMARY_ID,
    );
  });

  it("wires its hover-revealed description via aria-describedby for assistive technology", () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const trigger = screen.getByTestId("hidden-repo-dialog-trigger");
    const describedBy = trigger.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "Repositories you've hidden — reopen this to restore one.",
    );
  });
});

describe("HiddenRepoDialog — open/close", () => {
  it("opens on a trigger click, showing an honest empty state distinct from an empty list", async () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-dialog-trigger"));
    expect(screen.getByTestId("hidden-repo-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("hidden-repo-empty")).toHaveTextContent("No repositories are hidden.");
  });

  it("shows no empty-state line once at least one repo is hidden", async () => {
    render(<HiddenRepoDialog hiddenEngines={[engine("acme", "widget")]} hiddenCount={1} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-dialog-trigger"));
    expect(screen.queryByTestId("hidden-repo-empty")).not.toBeInTheDocument();
  });

  it("closes via the close button, returning focus to the trigger", async () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const user = userEvent.setup();
    const trigger = screen.getByTestId("hidden-repo-dialog-trigger");
    await user.click(trigger);
    await user.click(screen.getByTestId("hidden-repo-dialog-close"));
    expect(screen.queryByTestId("hidden-repo-dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("Ctrl+Enter inside the dialog has no confirm step to wire to and does not close it", async () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-dialog-trigger"));
    const container = screen.getByTestId("hidden-repo-dialog").querySelector("div");
    if (container === null) throw new Error("dialog container div not found");
    fireEvent.keyDown(container, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("hidden-repo-dialog")).toBeInTheDocument();
  });

  it("closes on the dialog's native cancel event", async () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-dialog-trigger"));
    const dialog = screen.getByTestId("hidden-repo-dialog");
    fireEvent(dialog, new Event("cancel"));
    expect(screen.queryByTestId("hidden-repo-dialog")).not.toBeInTheDocument();
  });

  it("closes on a backdrop click", async () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-dialog-trigger"));
    const dialog = screen.getByTestId("hidden-repo-dialog");
    fireEvent.click(dialog);
    expect(screen.queryByTestId("hidden-repo-dialog")).not.toBeInTheDocument();
  });

  it("a click inside the dialog content does not close it", async () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-dialog-trigger"));
    const dialog = screen.getByTestId("hidden-repo-dialog");
    fireEvent.click(within(dialog).getByText("Hidden repositories (0)"));
    expect(screen.getByTestId("hidden-repo-dialog")).toBeInTheDocument();
  });

  it("Escape closes the dialog and returns focus to the trigger", async () => {
    render(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    const user = userEvent.setup();
    const trigger = screen.getByTestId("hidden-repo-dialog-trigger");
    await user.click(trigger);
    const container = screen.getByTestId("hidden-repo-dialog").querySelector("div");
    if (container === null) throw new Error("dialog container div not found");
    fireEvent.keyDown(container, { key: "Escape" });
    expect(screen.queryByTestId("hidden-repo-dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});

describe("HiddenRepoDialog — restore", () => {
  async function openDialog(): Promise<void> {
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-dialog-trigger"));
  }

  it("lists each hidden repo with its own Restore control", async () => {
    render(
      <HiddenRepoDialog
        hiddenEngines={[engine("acme", "widget"), engine("acme", "sprocket")]}
        hiddenCount={2}
      />,
    );
    await openDialog();
    expect(screen.getByTestId("hidden-repo-row-acme/widget")).toHaveTextContent("acme/widget");
    expect(screen.getByTestId("hidden-repo-restore-acme/widget")).toBeInTheDocument();
    expect(screen.getByTestId("hidden-repo-row-acme/sprocket")).toHaveTextContent("acme/sprocket");
    expect(screen.getByTestId("hidden-repo-restore-acme/sprocket")).toBeInTheDocument();
  });

  it("restoring calls the unhide endpoint, disables the control meanwhile, and announces success", async () => {
    let resolveRestore: (() => void) | null = null;
    server.use(
      http.post("/api/engines/acme/widget/unhide", async () => {
        await new Promise<void>((resolve) => {
          resolveRestore = resolve;
        });
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "propose" });
      }),
    );
    render(
      <>
        <HiddenRepoDialog hiddenEngines={[engine("acme", "widget")]} hiddenCount={1} />
        <HiddenRepoAnnouncementRegion />
      </>,
    );
    await openDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-restore-acme/widget"));
    expect(screen.getByTestId("hidden-repo-restore-acme/widget")).toBeDisabled();
    expect(screen.getByTestId("hidden-repo-restore-acme/widget")).toHaveTextContent("Restoring…");
    act(() => resolveRestore?.());
    await waitFor(() =>
      expect(screen.getByTestId("hidden-repo-announcement")).toHaveTextContent("acme/widget restored."),
    );
    const announcement = screen.getByTestId("hidden-repo-announcement");
    expect(announcement.className).not.toContain("sr-only");
    expect(announcement.querySelector("span")).toHaveClass("text-text-primary");
  });

  it("a failed restore surfaces an inline error and re-enables the control", async () => {
    server.use(
      http.post("/api/engines/acme/widget/unhide", () =>
        HttpResponse.json({ detail: "unhide-boom" }, { status: 500 }),
      ),
    );
    render(
      <>
        <HiddenRepoDialog hiddenEngines={[engine("acme", "widget")]} hiddenCount={1} />
        <HiddenRepoAnnouncementRegion />
      </>,
    );
    await openDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-restore-acme/widget"));
    const row = await screen.findByTestId("hidden-repo-row-acme/widget");
    expect(within(row).getByText(/unhide-boom/)).toBeInTheDocument();
    expect(screen.getByTestId("hidden-repo-restore-acme/widget")).not.toBeDisabled();
    await waitFor(() =>
      expect(screen.getByTestId("hidden-repo-announcement")).toHaveTextContent(
        "acme/widget failed to restore.",
      ),
    );
    const announcement = screen.getByTestId("hidden-repo-announcement");
    expect(announcement.className).not.toContain("sr-only");
    expect(announcement.querySelector("span")).toHaveClass("font-medium", "text-btn-destroy");
  });

  it("maps a non-Error rejection to String(err) in the inline error", async () => {
    const apiModule = await import("../api");
    const original = apiModule.api.unhideEngine;
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    apiModule.api.unhideEngine = () => Promise.reject("network-fell-over");
    try {
      render(<HiddenRepoDialog hiddenEngines={[engine("acme", "widget")]} hiddenCount={1} />);
      await openDialog();
      const user = userEvent.setup();
      await user.click(screen.getByTestId("hidden-repo-restore-acme/widget"));
      const row = await screen.findByTestId("hidden-repo-row-acme/widget");
      expect(within(row).getByText("network-fell-over")).toBeInTheDocument();
    } finally {
      apiModule.api.unhideEngine = original;
    }
  });

  it("moves focus to the next Restore control when a row disappears after its own restore completes", async () => {
    const { rerender } = render(
      <HiddenRepoDialog
        hiddenEngines={[engine("acme", "widget"), engine("acme", "sprocket")]}
        hiddenCount={2}
      />,
    );
    await openDialog();
    screen.getByTestId("hidden-repo-restore-acme/widget").focus();
    rerender(<HiddenRepoDialog hiddenEngines={[engine("acme", "sprocket")]} hiddenCount={1} />);
    await waitFor(() =>
      expect(screen.getByTestId("hidden-repo-restore-acme/sprocket")).toHaveFocus(),
    );
  });

  it("does not steal focus when hiddenEngines changes without the focused control disappearing", async () => {
    const { rerender } = render(
      <HiddenRepoDialog hiddenEngines={[engine("acme", "widget")]} hiddenCount={1} />,
    );
    await openDialog();
    screen.getByTestId("hidden-repo-dialog-close").focus();
    rerender(
      <HiddenRepoDialog
        hiddenEngines={[engine("acme", "widget"), engine("acme", "sprocket")]}
        hiddenCount={2}
      />,
    );
    expect(screen.getByTestId("hidden-repo-dialog-close")).toHaveFocus();
  });

  it("moves focus to the Close button when the last hidden repo's row disappears", async () => {
    const { rerender } = render(
      <HiddenRepoDialog hiddenEngines={[engine("acme", "widget")]} hiddenCount={1} />,
    );
    await openDialog();
    screen.getByTestId("hidden-repo-restore-acme/widget").focus();
    rerender(<HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />);
    await waitFor(() => expect(screen.getByTestId("hidden-repo-dialog-close")).toHaveFocus());
  });

  it("restoring one repo does not disable a different repo's Restore control", async () => {
    let resolveOther: (() => void) | null = null;
    server.use(
      http.post("/api/engines/acme/widget/unhide", async () => {
        await new Promise<void>((resolve) => {
          resolveOther = resolve;
        });
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "propose" });
      }),
    );
    render(
      <HiddenRepoDialog
        hiddenEngines={[engine("acme", "widget"), engine("acme", "sprocket")]}
        hiddenCount={2}
      />,
    );
    await openDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("hidden-repo-restore-acme/widget"));
    expect(screen.getByTestId("hidden-repo-restore-acme/sprocket")).not.toBeDisabled();
    await act(async () => {
      resolveOther?.();
      await Promise.resolve();
    });
  });
});

describe("HiddenRepoAnnouncementRegion — cross-subtree announcement channel", () => {
  it("is sr-only with no visible banner before any announcement arrives", () => {
    render(<HiddenRepoAnnouncementRegion />);
    expect(screen.getByTestId("hidden-repo-announcement").className).toContain("sr-only");
  });

  it("renders text handed to it via announceHiddenRepoChange from anywhere, including while the dialog is closed", () => {
    render(<HiddenRepoAnnouncementRegion />);
    act(() => {
      announceHiddenRepoChange({ text: "acme/widget hidden — everything stopped cleanly.", tone: "ok" });
    });
    expect(screen.getByTestId("hidden-repo-announcement")).toHaveTextContent(
      "acme/widget hidden — everything stopped cleanly.",
    );
  });

  it("switches from sr-only to a visible banner once populated, and back once dismissed", () => {
    render(<HiddenRepoAnnouncementRegion />);
    act(() => {
      announceHiddenRepoChange({ text: "acme/widget hidden.", tone: "ok" });
    });
    const announcement = screen.getByTestId("hidden-repo-announcement");
    expect(announcement.className).not.toContain("sr-only");
    fireEvent.click(screen.getByTestId("hidden-repo-announcement-dismiss"));
    expect(screen.getByTestId("hidden-repo-announcement").className).toContain("sr-only");
  });

  it("renders a warn tone with the amber warning-text token, distinct from an error", () => {
    render(<HiddenRepoAnnouncementRegion />);
    act(() => {
      announceHiddenRepoChange({ text: "Hidden, but not everything stopped.", tone: "warn" });
    });
    expect(screen.getByTestId("hidden-repo-announcement").querySelector("span")).toHaveClass(
      "font-medium",
      "text-status-warning-fg",
    );
  });

  it("renders an error tone with the same destructive text token", () => {
    render(<HiddenRepoAnnouncementRegion />);
    act(() => {
      announceHiddenRepoChange({ text: "Hide failed — boom.", tone: "error" });
    });
    expect(screen.getByTestId("hidden-repo-announcement").querySelector("span")).toHaveClass(
      "font-medium",
      "text-btn-destroy",
    );
  });

  it("rewrites a second announcement in place rather than appending", () => {
    render(<HiddenRepoAnnouncementRegion />);
    act(() => {
      announceHiddenRepoChange({ text: "first announcement", tone: "ok" });
    });
    act(() => {
      announceHiddenRepoChange({ text: "second announcement", tone: "ok" });
    });
    const announcement = screen.getByTestId("hidden-repo-announcement");
    expect(announcement).toHaveTextContent("second announcement");
    expect(announcement).not.toHaveTextContent("first announcement");
  });

  it("stops updating once unmounted, so a stale listener never sets state on a gone component", () => {
    const { unmount } = render(<HiddenRepoAnnouncementRegion />);
    unmount();
    expect(() => {
      act(() => {
        announceHiddenRepoChange({ text: "late announcement", tone: "ok" });
      });
    }).not.toThrow();
  });
});
