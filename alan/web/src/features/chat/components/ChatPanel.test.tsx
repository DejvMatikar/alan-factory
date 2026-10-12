import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ChatPanel from "./ChatPanel";
import type { ChatComposerActions } from "../hooks/useChatComposerActions";
import type { ToolActivity } from "../hooks/useChatSessionGeneric";
import type { ChatMessage, RepoProvisionStatus } from "../../../api";
import { AUTO_DISPATCH_IDLE } from "../hooks/autoDispatch";
import type { ChatTurn } from "../hooks/chatSessionReducer";
import { JUMP_TO_LATEST_CLASS, SCROLL_BODY_FLOW_CLASS } from "../../../shell/stickyOffsets";

function makeActions(overrides: Partial<ChatComposerActions> = {}): ChatComposerActions {
  return {
    draft: "",
    setDraft: vi.fn(),
    summarizing: false,
    summarizeError: null,
    stopping: false,
    handleSend: vi.fn(),
    handleClear: vi.fn(),
    handleSummarize: vi.fn(),
    handleBriefEdit: vi.fn(),
    handleStop: vi.fn(),
    runSummarize: vi.fn(),
    ...overrides,
  };
}

const EMPTY_CHAT = {
  messages: [] as ChatMessage[],
  toolActivity: [] as ToolActivity[],
  turn: { kind: "idle" } as ChatTurn,
  queuedMessage: null,
  cancelQueued: vi.fn(),
  repoProvision: null as RepoProvisionStatus | null,
};

type PanelProps = Omit<
  React.ComponentProps<typeof ChatPanel>,
  "onAutoDispatch" | "autoDispatch" | "pinned" | "onJumpToLatest"
> &
  Partial<Pick<React.ComponentProps<typeof ChatPanel>, "pinned" | "onJumpToLatest">>;

function renderPanel(props: PanelProps) {
  return render(
    <ChatPanel
      {...props}
      onAutoDispatch={vi.fn()}
      autoDispatch={AUTO_DISPATCH_IDLE}
      pinned={props.pinned ?? true}
      onJumpToLatest={props.onJumpToLatest ?? vi.fn()}
    />,
  );
}

describe("ChatPanel", () => {
  it("renders the empty hint when there are no messages", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "start typing",
      sessionReady: true,
    });
    expect(screen.getByTestId("chat-empty")).toHaveTextContent("start typing");
  });

  it("disables the composer when sessionReady is false", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: false,
    });
    expect(screen.getByLabelText("Message")).toBeDisabled();
  });

  it("enables the composer when sessionReady is true", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.getByLabelText("Message")).not.toBeDisabled();
  });

  it("uses the default rows value of 6 when rows is not provided", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.getByLabelText<HTMLTextAreaElement>("Message").rows).toBe(6);
  });

  it("uses an explicit rows value when provided", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
      rows: 12,
    });
    expect(screen.getByLabelText<HTMLTextAreaElement>("Message").rows).toBe(12);
  });

  it("shows the chat error banner when the turn has failed", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: { ...EMPTY_CHAT, turn: { kind: "failed", error: "chat went wrong" } },
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.getByText("chat went wrong")).toBeInTheDocument();
  });

  it("shows no banner and no thinking indicator for an idle turn", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: { ...EMPTY_CHAT, turn: { kind: "idle" } },
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.queryByTestId("thinking")).not.toBeInTheDocument();
  });

  it("shows the thinking indicator and no banner for a running turn with no notice", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: { ...EMPTY_CHAT, turn: { kind: "running", notice: null } },
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.getByTestId("thinking")).toBeInTheDocument();
  });

  it("shows both the thinking indicator and the error banner for a running turn with a notice", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: { ...EMPTY_CHAT, turn: { kind: "running", notice: "connection dropped" } },
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.getByTestId("thinking")).toBeInTheDocument();
    expect(screen.getByText("connection dropped")).toBeInTheDocument();
  });

  it("shows the summarize error banner when actions.summarizeError is non-null", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions({ summarizeError: "summarize failed" }),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.getByText("summarize failed")).toBeInTheDocument();
  });

  it("shows no repo-provision banner on ready/not_needed/null", () => {
    for (const status of ["ready", "not_needed", null] as (RepoProvisionStatus | null)[]) {
      const { unmount } = renderPanel({
        scrollAnchorRef: createRef(),
        chat: { ...EMPTY_CHAT, repoProvision: status },
        actions: makeActions(),
        emptyHint: "hint",
        sessionReady: true,
      });
      expect(screen.queryByTestId("repo-provision-pending")).not.toBeInTheDocument();
      expect(screen.queryByTestId("repo-provision-error")).not.toBeInTheDocument();
      unmount();
    }
  });

  it("shows an accessible in-progress status when repo_provision is pending", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: { ...EMPTY_CHAT, repoProvision: "pending" },
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    const banner = screen.getByTestId("repo-provision-pending");
    expect(banner).toHaveAttribute("role", "status");
    expect(banner).toHaveAttribute("aria-live", "polite");
  });

  it("shows an accessible error banner with a retry affordance when repo_provision is error", async () => {
    const user = userEvent.setup();
    const onRetryProvisioning = vi.fn();
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: { ...EMPTY_CHAT, repoProvision: "error" },
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
      onRetryProvisioning,
    });
    const banner = screen.getByTestId("repo-provision-error");
    expect(banner).toHaveAttribute("role", "alert");
    await user.click(screen.getByTestId("repo-provision-retry"));
    expect(onRetryProvisioning).toHaveBeenCalledTimes(1);
  });

  it("omits the retry button when onRetryProvisioning is not provided", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: { ...EMPTY_CHAT, repoProvision: "error" },
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.getByTestId("repo-provision-error")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-provision-retry")).not.toBeInTheDocument();
  });

  it("renders no delete button or delete error banner when deleteAction is omitted", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.queryByTestId("delete-chat-button")).not.toBeInTheDocument();
  });

  it("renders the delete button and surfaces deleteError in a banner when deleteAction is provided", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
      deleteAction: { onRequestDelete: vi.fn(), deleting: false, deleteError: "delete failed" },
    });
    expect(screen.getByTestId("delete-chat-button")).toBeInTheDocument();
    expect(screen.getByText("delete failed")).toBeInTheDocument();
  });

  it("renders the auto-dispatch button", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    expect(screen.getByTestId("auto-dispatch-button")).toBeInTheDocument();
  });

  it("wraps only the transcript in the page-flow class, leaving the composer outside it as an unconstrained sibling", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
    });
    const transcript = screen.getByTestId("chat-transcript");
    const wrapper = transcript.parentElement!;
    expect(wrapper.className).toBe(SCROLL_BODY_FLOW_CLASS);
    const composerField = screen.getByLabelText("Message");
    expect(wrapper.contains(composerField)).toBe(false);
  });

  it("shows no Jump to latest control when pinned to the bottom", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
      pinned: true,
    });
    expect(screen.queryByTestId("chat-jump-to-latest")).not.toBeInTheDocument();
  });

  it("shows the Jump to latest control when scrolled away from the bottom, and calls onJumpToLatest on click", async () => {
    const user = userEvent.setup();
    const onJumpToLatest = vi.fn();
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
      pinned: false,
      onJumpToLatest,
    });
    const control = screen.getByTestId("chat-jump-to-latest");
    expect(control).toHaveTextContent("Jump to latest");
    for (const token of JUMP_TO_LATEST_CLASS.split(" ")) {
      expect(control.className).toContain(token);
    }
    await user.click(control);
    expect(onJumpToLatest).toHaveBeenCalledTimes(1);
  });

  it("keeps the Jump to latest control out of the tab order while hidden", () => {
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
      pinned: true,
    });
    expect(
      screen.queryByRole("button", { name: /jump to latest/i }),
    ).not.toBeInTheDocument();
  });

  it("makes the Jump to latest control keyboard reachable and activatable while shown", async () => {
    const user = userEvent.setup();
    const onJumpToLatest = vi.fn();
    renderPanel({
      scrollAnchorRef: createRef(),
      chat: EMPTY_CHAT,
      actions: makeActions(),
      emptyHint: "hint",
      sessionReady: true,
      pinned: false,
      onJumpToLatest,
    });
    const control = screen.getByRole("button", { name: /jump to latest/i });
    control.focus();
    expect(control).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onJumpToLatest).toHaveBeenCalledTimes(1);
  });
});
