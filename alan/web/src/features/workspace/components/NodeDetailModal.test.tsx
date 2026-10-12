import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { api, type NodeDetailTab, type NodeConfigDoc, type NodeResultDoc } from "../../../api";
import { RUN_LAUNCH_FIELD_IDS } from "../../../components/LaunchSetupList";
import { makeLaunchRecord } from "../../../test/agentFixtures";
import NodeDetailModal from "./NodeDetailModal";

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute("open", "");
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute("open");
    };
  }
});

const RESULT_DOC: NodeResultDoc = {
  role: "worker",
  model: "sonnet",
  result_text: "## Output\nDone",
  completed_at: "2026-05-12T08:00:10Z",
};

const CONFIG_DOC: NodeConfigDoc = {
  system_prompt: "Be helpful",
  user_text: "Do something",
  allowed_tools: ["Bash", "Read"],
  max_turns: 10,
  permission_mode: "auto",
  model: "sonnet",
  turns_source: "static",
  derived_max_turns: 10,
};

function renderModal(
  initialTab: NodeDetailTab = "output",
  onClose = vi.fn(),
  stepLabel = "worker",
) {
  render(
    <NodeDetailModal
      taskId="t1"
      invId="inv_001"
      nodeId="worker"
      stepLabel={stepLabel}
      initialTab={initialTab}
      onClose={onClose}
    />,
  );
  return { onClose };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("NodeDetailModal", () => {
  it("renders dialog with nodeId and all tab buttons", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    expect(screen.getByTestId("node-detail-dialog")).toBeInTheDocument();
    expect(screen.getByText("worker")).toBeInTheDocument();
    expect(screen.getByTestId("tab-output")).toBeInTheDocument();
    expect(screen.getByTestId("tab-prompt")).toBeInTheDocument();
    expect(screen.getByTestId("tab-config")).toBeInTheDocument();
  });

  it("output tab active class differs from inactive tabs", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal("output");
    expect(screen.getByTestId("tab-output").className).toContain("border-b-2");
    expect(screen.getByTestId("tab-prompt").className).not.toContain("border-b-2");
    expect(screen.getByTestId("tab-config").className).not.toContain("border-b-2");
  });

  it("shows output-loading immediately when output tab is initial", () => {
    vi.spyOn(api, "fetchNodeResult").mockReturnValue(new Promise<NodeResultDoc>(() => undefined));
    renderModal("output");
    expect(screen.getByTestId("output-loading")).toBeInTheDocument();
  });

  it("shows output-content after successful fetch", async () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal("output");
    await screen.findByTestId("output-content");
  });

  it("shows output-error when fetch rejects with Error", async () => {
    vi.spyOn(api, "fetchNodeResult").mockRejectedValue(new Error("not found"));
    renderModal("output");
    const err = await screen.findByTestId("output-error");
    expect(err).toHaveTextContent("not found");
  });

  it("shows output-error with String(err) when non-Error thrown", async () => {
    vi.spyOn(api, "fetchNodeResult").mockRejectedValue("plain error");
    renderModal("output");
    const err = await screen.findByTestId("output-error");
    expect(err).toHaveTextContent("plain error");
  });

  it("shows prompt-loading immediately when prompt tab is initial", () => {
    vi.spyOn(api, "fetchNodeConfig").mockReturnValue(new Promise<NodeConfigDoc>(() => undefined));
    renderModal("prompt");
    expect(screen.getByTestId("prompt-loading")).toBeInTheDocument();
  });

  it("shows prompt-content after successful config fetch", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("prompt");
    const content = await screen.findByTestId("prompt-content");
    expect(content).toHaveTextContent("Be helpful");
    expect(content).toHaveTextContent("Do something");
  });

  it("shows prompt-error when config fetch rejects with Error", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockRejectedValue(new Error("forbidden"));
    renderModal("prompt");
    await screen.findByTestId("prompt-error");
  });

  it("shows prompt-error with String(err) when non-Error thrown", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockRejectedValue("plain config error");
    renderModal("prompt");
    const err = await screen.findByTestId("prompt-error");
    expect(err).toHaveTextContent("plain config error");
  });

  it("shows config-loading immediately when config tab is initial", () => {
    vi.spyOn(api, "fetchNodeConfig").mockReturnValue(new Promise<NodeConfigDoc>(() => undefined));
    renderModal("config");
    expect(screen.getByTestId("config-loading")).toBeInTheDocument();
  });

  it("shows config-content with non-empty allowed_tools", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("config");
    const content = await screen.findByTestId("config-content");
    expect(content).toHaveTextContent("Bash, Read");
  });

  it("shows (none) when allowed_tools is empty", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue({ ...CONFIG_DOC, allowed_tools: [] });
    renderModal("config");
    await screen.findByTestId("config-content");
    expect(screen.getByText("(none)")).toBeInTheDocument();
  });

  it("omits the adaptive-ceiling row when turns_source is static", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("config");
    await screen.findByTestId("config-content");
    expect(screen.queryByTestId("config-adaptive-ceiling")).not.toBeInTheDocument();
  });

  it("shows the adaptive-ceiling row with derived vs applied turns when turns_source is adaptive", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue({
      ...CONFIG_DOC,
      turns_source: "adaptive",
      derived_max_turns: 7,
      max_turns: 10,
    });
    renderModal("config");
    const row = await screen.findByTestId("config-adaptive-ceiling");
    expect(row).toHaveTextContent("Adaptive ceiling");
    expect(row).toHaveTextContent("derived 7, applied 10");
  });

  it("shows config-error when config fetch fails", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockRejectedValue(new Error("gone"));
    renderModal("config");
    await screen.findByTestId("config-error");
  });

  it("switching tabs triggers a new fetch", async () => {
    const fetchResult = vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const fetchConfig = vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("output");
    await screen.findByTestId("output-content");
    expect(fetchResult).toHaveBeenCalledTimes(1);
    expect(fetchConfig).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("tab-prompt"));
    await screen.findByTestId("prompt-content");
    expect(fetchConfig).toHaveBeenCalledTimes(1);
  });

  it("config tab shares the prompt fetch (single config fetch for both)", async () => {
    const fetchConfig = vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("prompt");
    await screen.findByTestId("prompt-content");
    expect(fetchConfig).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByTestId("tab-config"));
    await screen.findByTestId("config-content");
    expect(fetchConfig).toHaveBeenCalledTimes(1);
  });

  it("re-visiting a tab does not re-fetch", async () => {
    const fetchResult = vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const fetchConfig = vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("output");
    await screen.findByTestId("output-content");
    await userEvent.click(screen.getByTestId("tab-prompt"));
    await screen.findByTestId("prompt-content");
    await userEvent.click(screen.getByTestId("tab-output"));
    expect(screen.getByTestId("output-content")).toBeInTheDocument();
    expect(fetchResult).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByTestId("tab-config"));
    expect(screen.getByTestId("config-content")).toBeInTheDocument();
    expect(fetchConfig).toHaveBeenCalledTimes(1);
  });

  it("close button calls onClose", async () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { onClose } = renderModal();
    await userEvent.click(screen.getByTestId("node-detail-close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("Escape key calls onClose exactly once and marks the key event handled", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { onClose } = renderModal();
    const notPrevented = fireEvent.keyDown(screen.getByTestId("node-detail-close"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(notPrevented).toBe(false);
  });

  it("non-Escape key does not call onClose", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { onClose } = renderModal();
    fireEvent.keyDown(screen.getByTestId("node-detail-close"), { key: "Enter" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("native dialog cancel event is prevented and calls onClose once", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { onClose } = renderModal();
    const notPrevented = fireEvent(
      screen.getByTestId("node-detail-dialog"),
      new Event("cancel", { cancelable: true }),
    );
    expect(notPrevented).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("dialog is shown on mount and closed on unmount", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const { unmount } = render(
      <NodeDetailModal
        taskId="t1"
        invId="inv_001"
        nodeId="worker"
        stepLabel="worker"
        initialTab="output"
        onClose={vi.fn()}
      />,
    );
    expect(showModal).toHaveBeenCalled();
    unmount();
    expect(close).toHaveBeenCalled();
  });
});

describe("NodeDetailModal accessibility", () => {
  it("names the dialog after the step through aria-labelledby", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal("output", vi.fn(), "reviewer (step_2)");
    const dialog = screen.getByTestId("node-detail-dialog");
    const heading = document.getElementById(dialog.getAttribute("aria-labelledby")!)!;
    expect(heading.tagName).toBe("H2");
    expect(heading).toHaveTextContent("reviewer (step_2)");
    expect(dialog).toHaveAccessibleName("reviewer (step_2)");
  });

  it("exposes Output, Prompt and Config as tabs inside a labelled tablist", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    const tablist = screen.getByRole("tablist", { name: "Step details" });
    expect(within(tablist).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Output",
      "Prompt",
      "Config",
    ]);
  });

  it("marks only the initial tab as selected", () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("prompt");
    expect(screen.getByTestId("tab-prompt")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("tab-output")).toHaveAttribute("aria-selected", "false");
    expect(screen.getByTestId("tab-config")).toHaveAttribute("aria-selected", "false");
  });

  it("uses the shared underline tab treatment", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    expect(screen.getByTestId("tab-output").className).toContain("border-b-2 border-text-primary");
    expect(screen.getByTestId("tab-prompt").className).toContain("text-text-muted");
  });

  it("labels the tabpanel by the active tab", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", "tab-output");
    expect(panel).toHaveAccessibleName("Output");
  });

  it("moves to the next tab with ArrowRight and shows its panel", async () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal();
    await userEvent.keyboard("{ArrowRight}");
    expect(await screen.findByTestId("prompt-content")).toBeInTheDocument();
    expect(screen.getByTestId("tab-prompt")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Prompt");
  });

  it("moves back with ArrowLeft", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("config");
    await userEvent.keyboard("{ArrowLeft}");
    expect(await screen.findByTestId("prompt-content")).toBeInTheDocument();
    expect(screen.getByTestId("tab-prompt")).toHaveAttribute("aria-selected", "true");
  });

  it("focuses the initially selected tab after mount", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    expect(screen.getByTestId("tab-output")).toHaveFocus();
  });

  it("focuses the initially selected tab when it is not the first", () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("config");
    expect(screen.getByTestId("tab-config")).toHaveFocus();
  });

  it("renders the close control as the shared icon-only button", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    const close = screen.getByTestId("node-detail-close");
    expect(close).toHaveAttribute("data-icon-only", "true");
    expect(close).toHaveAccessibleName("Close");
    expect(close).toHaveAttribute("aria-keyshortcuts", "Escape");
  });

  it("shows a visible Esc hint on the close control", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    expect(within(screen.getByTestId("node-detail-close")).getByText("Esc")).toBeVisible();
  });

  it("describes the close control on hover", async () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    const close = screen.getByTestId("node-detail-close");
    const description = document.getElementById(close.getAttribute("aria-describedby")!)!;
    expect(description).toHaveClass("sr-only");
    await userEvent.hover(close);
    expect(description).not.toHaveClass("sr-only");
    expect(description).toHaveTextContent("Close this pop-up");
  });

  it("describes the close control on keyboard focus", async () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    renderModal();
    const close = screen.getByTestId("node-detail-close");
    close.focus();
    await waitFor(() => {
      expect(document.getElementById(close.getAttribute("aria-describedby")!)).not.toHaveClass("sr-only");
    });
  });

  it("dismisses the focused close control's description on the first Escape and closes on the second", async () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { onClose } = renderModal();
    const close = screen.getByTestId("node-detail-close");
    const description = document.getElementById(close.getAttribute("aria-describedby")!)!;
    close.focus();
    await waitFor(() => {
      expect(description).not.toHaveClass("sr-only");
    });
    await userEvent.keyboard("{Escape}");
    expect(description).toHaveClass("sr-only");
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("swallows Ctrl+Enter without closing", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { onClose } = renderModal();
    const notPrevented = fireEvent.keyDown(screen.getByTestId("tab-output"), {
      key: "Enter",
      ctrlKey: true,
    });
    expect(notPrevented).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("moves focus to the selected tab again when the modal is retargeted to another node", () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { rerender } = render(
      <NodeDetailModal
        taskId="t1"
        invId="inv_001"
        nodeId="worker-a"
        stepLabel="worker-a"
        initialTab="output"
        onClose={vi.fn()}
      />,
    );
    screen.getByTestId("node-detail-close").focus();
    rerender(
      <NodeDetailModal
        taskId="t1"
        invId="inv_001"
        nodeId="worker-b"
        stepLabel="worker-b"
        initialTab="output"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tab-output")).toHaveFocus();
  });

  it("closes on the first Escape pressed from the selected tab", async () => {
    vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { onClose } = renderModal();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("NodeDetailModal refetch behaviors", () => {
  it("changing nodeId on a mounted modal resets state and refetches", async () => {
    const fetchResult = vi.spyOn(api, "fetchNodeResult").mockResolvedValue(RESULT_DOC);
    const { rerender } = render(
      <NodeDetailModal
        taskId="t1"
        invId="inv_001"
        nodeId="worker-a"
        stepLabel="worker-a"
        initialTab="output"
        onClose={vi.fn()}
      />,
    );
    await screen.findByTestId("output-content");
    expect(fetchResult).toHaveBeenCalledTimes(1);
    rerender(
      <NodeDetailModal
        taskId="t1"
        invId="inv_001"
        nodeId="worker-b"
        stepLabel="worker-b"
        initialTab="output"
        onClose={vi.fn()}
      />,
    );
    await screen.findByTestId("output-content");
    expect(fetchResult).toHaveBeenCalledTimes(2);
  });

  it("switching away from a failed tab and back retries the fetch", async () => {
    const fetchResult = vi
      .spyOn(api, "fetchNodeResult")
      .mockRejectedValueOnce(new Error("transient"))
      .mockResolvedValueOnce(RESULT_DOC);
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("output");
    await screen.findByTestId("output-error");
    expect(fetchResult).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByTestId("tab-prompt"));
    await screen.findByTestId("prompt-content");
    await userEvent.click(screen.getByTestId("tab-output"));
    await screen.findByTestId("output-content");
    expect(fetchResult).toHaveBeenCalledTimes(2);
  });
});

describe("NodeDetailModal recorded launch setup", () => {
  const LAUNCH_DOC: NodeConfigDoc = { ...CONFIG_DOC, launch: makeLaunchRecord() };

  it("shows the resolved system prompt with its tail segments as plain text, and the first user message", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(LAUNCH_DOC);
    renderModal("prompt");
    const resolved = await screen.findByTestId("prompt-resolved");
    expect(within(resolved).getByTestId("prompt-resolved-text").tagName).toBe("PRE");
    expect(resolved).toHaveTextContent("You are the implementer.");
    expect(resolved).toHaveTextContent("Turn budget for this run: 40");
    expect(screen.getByTestId("prompt-user-text")).toHaveTextContent("Do something");
    expect(screen.getByText("First user message")).toBeInTheDocument();
  });

  it("does not repeat the stored system prompt or claim the launch was not recorded", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(LAUNCH_DOC);
    renderModal("prompt");
    await screen.findByTestId("prompt-resolved");
    expect(screen.queryByTestId("prompt-recorded-system")).not.toBeInTheDocument();
    expect(screen.queryByTestId("prompt-resolved-not-recorded")).not.toBeInTheDocument();
    expect(screen.queryByText("Be helpful")).not.toBeInTheDocument();
  });

  it("shows every launch field with the value the CLI received, including the effort", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(LAUNCH_DOC);
    renderModal("config");
    await screen.findByTestId("config-launch");
    expect(screen.getByTestId("config-launch-model")).toHaveTextContent("sonnet");
    expect(screen.getByTestId("config-launch-effort")).toHaveTextContent("high");
    expect(screen.getByTestId("config-launch-turn_limit")).toHaveTextContent("40");
    expect(screen.getByTestId("config-launch-permission_mode")).toHaveTextContent("acceptEdits");
    expect(screen.getByTestId("config-launch-tools_effective")).toHaveTextContent("Edit");
    expect(screen.getByTestId("config-launch-tools_denied")).toHaveTextContent("WebFetch");
    expect(screen.getByTestId("config-launch-add_dirs")).toHaveTextContent("/srv/docs");
    expect(screen.getByTestId("config-launch-session")).toHaveTextContent("New session");
    expect(screen.getByTestId("config-launch-working_directory")).toHaveTextContent("/work/checkout");
    expect(screen.getByTestId("config-launch-timeouts")).toHaveTextContent("idle 600s");
    expect(screen.getByTestId("config-launch-environment")).toHaveTextContent("PATH (allowlisted)");
    expect(screen.getByTestId("config-launch-process")).toHaveTextContent("fleet worker");
    expect(screen.getByTestId("config-content")).not.toHaveTextContent("not recorded for this run");
  });

  it("still shows the stored tool list and the adaptive ceiling beside the recorded launch", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue({
      ...LAUNCH_DOC,
      turns_source: "adaptive",
      derived_max_turns: 7,
      max_turns: 10,
    });
    renderModal("config");
    await screen.findByTestId("config-launch");
    expect(screen.getByTestId("config-recorded")).toHaveTextContent("Allowed tools (stored)");
    expect(screen.getByTestId("config-recorded")).toHaveTextContent("Bash, Read");
    expect(screen.getByTestId("config-adaptive-ceiling")).toHaveTextContent("derived 7, applied 10");
    expect(screen.queryByText("Model (recorded)")).not.toBeInTheDocument();
  });
});

describe("NodeDetailModal records written before the launch was recorded", () => {
  it("says the resolved system prompt is not recorded and labels the old system prompt as recorded at step start", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("prompt");
    expect(await screen.findByTestId("prompt-resolved-not-recorded")).toHaveTextContent(
      "Resolved system prompt: not recorded for this run",
    );
    expect(screen.getByTestId("prompt-recorded-system")).toHaveTextContent("Be helpful");
    expect(screen.getByText("System prompt (recorded when the step started)")).toBeInTheDocument();
    expect(screen.queryByTestId("prompt-resolved")).not.toBeInTheDocument();
  });

  it("shows not recorded for every launch field and never a guessed value", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("config");
    await screen.findByTestId("config-launch");
    for (const id of RUN_LAUNCH_FIELD_IDS) {
      expect(screen.getByTestId(`config-launch-${id}`)).toHaveTextContent("not recorded for this run");
    }
    expect(screen.getByTestId("config-launch-effort")).not.toHaveTextContent("high");
  });

  it("keeps the old flat fields only under explicit recorded and stored labels", async () => {
    vi.spyOn(api, "fetchNodeConfig").mockResolvedValue(CONFIG_DOC);
    renderModal("config");
    await screen.findByTestId("config-recorded");
    const recorded = screen.getByTestId("config-recorded");
    expect(recorded).toHaveTextContent("Model (recorded)");
    expect(recorded).toHaveTextContent("Max turns (recorded)");
    expect(recorded).toHaveTextContent("Permission mode (recorded)");
    expect(recorded).toHaveTextContent("Allowed tools (stored)");
    expect(recorded).toHaveTextContent("Bash, Read");
  });
});

describe("NodeDetailModal unmount during fetch", () => {
  it("no-op when unmounted before output fetch resolves", async () => {
    let resolveResult!: (v: NodeResultDoc) => void;
    vi.spyOn(api, "fetchNodeResult").mockReturnValue(
      new Promise<NodeResultDoc>((r) => { resolveResult = r; }),
    );
    const { unmount } = render(
      <NodeDetailModal
        taskId="t1"
        invId="inv_001"
        nodeId="worker"
        stepLabel="worker"
        initialTab="output"
        onClose={vi.fn()}
      />,
    );
    unmount();
    resolveResult(RESULT_DOC);
    await waitFor(() => undefined);
  });

  it("no-op when unmounted before config fetch resolves", async () => {
    let resolveConfig!: (v: NodeConfigDoc) => void;
    vi.spyOn(api, "fetchNodeConfig").mockReturnValue(
      new Promise<NodeConfigDoc>((r) => { resolveConfig = r; }),
    );
    const { unmount } = render(
      <NodeDetailModal
        taskId="t1"
        invId="inv_001"
        nodeId="worker"
        stepLabel="worker"
        initialTab="prompt"
        onClose={vi.fn()}
      />,
    );
    unmount();
    resolveConfig(CONFIG_DOC);
    await waitFor(() => undefined);
  });
});
