import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, useState } from "react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { CounterCase, DispatchProposal, DispatchProposalResponse } from "../../../api";
import type { ProposalPanelDoorConfig } from "../components/ProposalPanel";
import type { ProposalFlowState } from "./proposalFlowReducer";
import { settleDefaultEngineKey } from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import {
  useDispatchProposalFlow,
  type DispatchWorkspaceFn,
  type UseDispatchProposalFlowOptions,
} from "./useDispatchProposalFlow";

const COUNTER_CASE: CounterCase = {
  against: "scope is still shifting under discussion",
  cheapest_80: "ship the read path only, defer writes",
  cost_estimate: "insufficient data",
};

const DOOR: ProposalPanelDoorConfig = {
  idleTestId: "harness-idle",
  idleLabel: "Do the thing",
  pendingLabel: "Preparing…",
  questionsTestId: "harness-questions",
  questionsActionLabel: "Answer and go",
  questionsBusyLabel: "Going…",
};

function readyResponse(overrides: Record<string, unknown> = {}): DispatchProposalResponse {
  return {
    status: "ready",
    proposal: {
      team: "brainstorming",
      rationale: "brief is ready to hand off",
      instructions: null,
      scope_audit: [],
      title: "explore-thing",
      acceptance_clauses: [],
      counter_case: COUNTER_CASE,
      ...overrides,
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  } as unknown as DispatchProposalResponse;
}

function needMoreInfo(questions: string[]): DispatchProposalResponse {
  return { status: "need_more_info", questions };
}

function isProposing(state: ProposalFlowState): boolean {
  return state.kind === "proposing" || (state.kind === "questions" && state.submit.kind === "submitting");
}

function proposeErrorOf(state: ProposalFlowState): string {
  if (state.kind === "propose_failed") return state.error;
  if (state.kind === "questions" && state.submit.kind === "failed") return state.submit.error;
  return "";
}

function dispatchErrorOf(state: ProposalFlowState): string {
  return state.kind === "dispatch_failed" ? state.error : "";
}

function questionsCountOf(state: ProposalFlowState): number {
  return state.kind === "questions" ? state.questions.length : 0;
}

interface HarnessProps {
  sessionId?: string | null;
  engineKey?: string | null;
  propose?: UseDispatchProposalFlowOptions["propose"];
  putBrief?: UseDispatchProposalFlowOptions["putBrief"];
  dispatchWorkspace?: DispatchWorkspaceFn;
  refreshRunSummary?: () => Promise<void>;
  readyOverrides?: Record<string, unknown>;
  workspaceDispatching?: boolean;
  workspaceDispatchError?: string | null;
}

function Harness(props: Readonly<HarnessProps>) {
  const [briefContent, setBriefContent] = useState("initial brief");
  const [commitResult, setCommitResult] = useState("");
  const [inFlight, setInFlight] = useState(0);
  const baseDispatch = props.dispatchWorkspace ?? (() => Promise.resolve(true));
  const dispatchWorkspace: DispatchWorkspaceFn = (title, description, options) => {
    setInFlight((c) => c + 1);
    return baseDispatch(title, description, options).finally(() => setInFlight((c) => c - 1));
  };
  const flow = useDispatchProposalFlow({
    sessionId: props.sessionId === undefined ? "sess-1" : props.sessionId,
    engineKey: props.engineKey,
    propose: props.propose ?? (() => Promise.reject(new Error("propose not stubbed"))),
    putBrief: props.putBrief ?? (() => Promise.reject(new Error("putBrief not stubbed"))),
    dispatchWorkspace,
    refreshRunSummary: props.refreshRunSummary ?? (() => Promise.resolve()),
    workspaceDispatching: props.workspaceDispatching ?? false,
    workspaceDispatchError: props.workspaceDispatchError ?? null,
    door: DOOR,
  });

  return createElement(
    "div",
    null,
    flow.renderProposal(briefContent),
    createElement(
      "button",
      { "data-testid": "propose-btn", onClick: () => flow.requestPropose(briefContent) },
      "propose",
    ),
    createElement(
      "button",
      {
        "data-testid": "resolve-immediate-btn",
        onClick: () => flow.resolveImmediate(readyResponse(props.readyOverrides ?? {}), briefContent),
      },
      "resolve-immediate",
    ),
    createElement(
      "button",
      { "data-testid": "edit-brief-btn", onClick: () => setBriefContent("edited brief") },
      "edit-brief",
    ),
    createElement(
      "button",
      { "data-testid": "blank-brief-btn", onClick: () => setBriefContent("   ") },
      "blank-brief",
    ),
    createElement(
      "button",
      { "data-testid": "submit-answers-direct-btn", onClick: () => flow.submitAnswers(briefContent) },
      "submit-answers-direct",
    ),
    createElement(
      "button",
      {
        "data-testid": "commit-btn",
        onClick: () => {
          const { proposal } = readyResponse(props.readyOverrides ?? {}) as unknown as {
            proposal: DispatchProposal;
          };
          const ready = {
            proposal,
            briefContent,
            dispatchDescription: briefContent,
            collision: { state: "green" as const, sentence: "", match: null },
          };
          void flow.commit(ready, null).then((ok) => setCommitResult(String(ok)));
        },
      },
      "commit",
    ),
    createElement("div", { "data-testid": "proposing" }, String(isProposing(flow.state))),
    createElement("div", { "data-testid": "propose-error" }, proposeErrorOf(flow.state)),
    createElement("div", { "data-testid": "dispatching" }, String(inFlight > 0)),
    createElement("div", { "data-testid": "dispatch-error" }, dispatchErrorOf(flow.state)),
    createElement("div", { "data-testid": "questions-count" }, String(questionsCountOf(flow.state))),
    createElement("div", { "data-testid": "commit-result" }, commitResult),
  );
}

describe("useDispatchProposalFlow", () => {
  it("resolveImmediate with a ready response renders the proposal fields inline with rationale and counter-case", async () => {
    render(createElement(Harness, {}));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    expect(screen.getByTestId("dispatch-proposal-rationale")).toHaveTextContent(
      "brief is ready to hand off",
    );
    expect(screen.getByTestId("dispatch-counter-case-against")).toHaveTextContent(
      COUNTER_CASE.against,
    );
    expect(screen.getByTestId("dispatch-proposal-confirm")).toBeInTheDocument();
  });

  it("a need_more_info propose response renders the questions body instead of the confirm button", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Which database?"])));
    render(createElement(Harness, { propose }));
    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));
    expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull();
    expect(screen.getByTestId(DOOR.questionsTestId)).toBeInTheDocument();
  });

  it("requestPropose toggles proposing while pending and renders ready on settle", async () => {
    let resolvePropose!: (res: DispatchProposalResponse) => void;
    const propose = vi.fn(
      () => new Promise<DispatchProposalResponse>((res) => { resolvePropose = res; }),
    );
    render(createElement(Harness, { propose }));
    await userEvent.click(screen.getByTestId("propose-btn"));
    expect(screen.getByTestId("proposing")).toHaveTextContent("true");
    resolvePropose(readyResponse({ title: "fresh-title" }));
    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.getByTestId("proposing")).toHaveTextContent("false");
    expect(propose).toHaveBeenCalledTimes(1);
  });

  it("requestPropose surfaces a propose failure via the error body", async () => {
    const propose = vi.fn(() => Promise.reject(new Error("claude failed")));
    render(createElement(Harness, { propose }));
    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("propose-error")).toHaveTextContent("claude failed"),
    );
    expect(screen.getByTestId("proposing")).toHaveTextContent("false");
    expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("claude failed");
  });

  it("a re-propose failure from a settled proposal shows the error, not the stale proposal", async () => {
    const propose = vi
      .fn<() => Promise<DispatchProposalResponse>>()
      .mockResolvedValueOnce(readyResponse({ title: "first" }))
      .mockRejectedValueOnce(new Error("re-propose broke"));
    render(createElement(Harness, { propose }));
    await userEvent.click(screen.getByTestId("propose-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("edit-brief-btn"));
    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("propose-error")).toHaveTextContent("re-propose broke"),
    );
    expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull();
    expect(screen.queryByTestId("dispatch-proposal-repropose")).toBeNull();
  });

  it("open questions stay visible while submitAnswers is in flight", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Which database?"])));
    let resolvePutBrief!: (v: { content: string }) => void;
    const putBrief = vi.fn(
      () => new Promise<{ content: string }>((res) => { resolvePutBrief = res; }),
    );
    render(createElement(Harness, { propose, putBrief }));
    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));
    await userEvent.type(screen.getByTestId("answer-0"), "Postgres");
    await userEvent.click(screen.getByTestId(DOOR.questionsTestId));
    expect(screen.getByTestId("questions-count")).toHaveTextContent("1");
    expect(screen.getByTestId(DOOR.questionsTestId)).toBeInTheDocument();
    expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull();
    resolvePutBrief({ content: "" });
  });

  it("submitAnswers defends against a missing answer entry, composing an empty string for it rather than crashing", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Which database?", "Which auth?"])));
    const putBrief = vi.fn((_sessionId: string, content: string) => Promise.resolve({ content }));
    render(createElement(Harness, { propose, putBrief }));

    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("2"));

    await userEvent.type(screen.getByTestId("answer-0"), "Postgres");
    propose.mockImplementation(() => Promise.resolve(readyResponse({ title: "answered-title" })));
    await userEvent.click(screen.getByTestId("submit-answers-direct-btn"));

    await screen.findByTestId("dispatch-proposal-confirm");
    const putBriefContent = putBrief.mock.calls[0]?.[1];
    expect(putBriefContent).toContain("Which database?");
    expect(putBriefContent).toContain("Postgres");
    expect(putBriefContent).toContain("Which auth?");
  });

  it("submitAnswers composes clarifications, puts the brief, re-proposes, and renders ready", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Which database?"])));
    const putBrief = vi.fn((_sessionId: string, content: string) => Promise.resolve({ content }));
    const refreshRunSummary = vi.fn(() => Promise.resolve());
    render(createElement(Harness, { propose, putBrief, refreshRunSummary }));

    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));

    propose.mockImplementation(() => Promise.resolve(readyResponse({ title: "answered-title" })));
    await userEvent.type(screen.getByTestId("answer-0"), "Postgres");
    await userEvent.click(screen.getByTestId(DOOR.questionsTestId));

    await screen.findByTestId("dispatch-proposal-confirm");
    expect(putBrief).toHaveBeenCalledTimes(1);
    const putBriefContent = putBrief.mock.calls[0]?.[1];
    expect(putBriefContent).toContain("Which database?");
    expect(putBriefContent).toContain("Postgres");
    expect(propose).toHaveBeenCalledTimes(2);
    expect(refreshRunSummary).toHaveBeenCalled();
  });

  it("a proposal reached by answering questions keeps the original brief as its staleness key but dispatches the composed clarifications", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Which database?"])));
    const putBrief = vi.fn((_sessionId: string, content: string) => Promise.resolve({ content }));
    const dispatchWorkspace = vi.fn<DispatchWorkspaceFn>(() => Promise.resolve(true));
    render(createElement(Harness, { propose, putBrief, dispatchWorkspace }));

    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));

    propose.mockImplementation(() => Promise.resolve(readyResponse({ title: "answered-title" })));
    await userEvent.type(screen.getByTestId("answer-0"), "Postgres");
    await userEvent.click(screen.getByTestId(DOOR.questionsTestId));

    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.queryByTestId("dispatch-proposal-repropose")).toBeNull();

    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(dispatchWorkspace).toHaveBeenCalledTimes(1));
    const dispatchedDescription = dispatchWorkspace.mock.calls[0]![1];
    expect(dispatchedDescription).toContain("## Clarifications");
    expect(dispatchedDescription).toContain("Postgres");
    expect(dispatchedDescription).not.toBe("initial brief");
  });

  it("submitAnswers surfaces a putBrief failure and leaves the questions and typed answers in place", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Why?"])));
    const putBrief = vi.fn(() => Promise.reject(new Error("save failed")));
    render(createElement(Harness, { propose, putBrief }));

    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));
    await userEvent.type(screen.getByTestId("answer-0"), "x");
    await userEvent.click(screen.getByTestId(DOOR.questionsTestId));

    await waitFor(() =>
      expect(screen.getByTestId("propose-error")).toHaveTextContent("save failed"),
    );
    expect(screen.getByTestId("questions-count")).toHaveTextContent("1");
    expect(screen.getByTestId("answer-0")).toHaveValue("x");
  });

  it("submitAnswers surfaces a re-propose failure", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Why?"])));
    const putBrief = vi.fn((_sessionId: string, content: string) =>
      Promise.resolve({ content }),
    );
    render(createElement(Harness, { propose, putBrief }));

    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));

    propose.mockImplementation(() => Promise.reject(new Error("re-propose failed")));
    await userEvent.type(screen.getByTestId("answer-0"), "x");
    await userEvent.click(screen.getByTestId(DOOR.questionsTestId));

    await waitFor(() =>
      expect(screen.getByTestId("propose-error")).toHaveTextContent("re-propose failed"),
    );
  });

  it("confirming a ready proposal calls dispatchWorkspace with the proposal's title/acceptance clauses/counter case", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.resolve(true));
    render(createElement(Harness, { dispatchWorkspace }));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(dispatchWorkspace).toHaveBeenCalledTimes(1));
    expect(dispatchWorkspace).toHaveBeenCalledWith("explore-thing", "initial brief", {
      acceptanceClauses: [],
      recordedClauses: [],
      counterCase: COUNTER_CASE,
      instructions: null,
      engineKey: undefined,
    });
    await waitFor(() => expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull());
  });

  it("commit() resolves true and calls dispatchWorkspace directly, without requiring a prior resolveImmediate/pendingReady flush", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.resolve(true));
    render(createElement(Harness, { dispatchWorkspace }));
    await userEvent.click(screen.getByTestId("commit-btn"));
    await waitFor(() => expect(screen.getByTestId("commit-result")).toHaveTextContent("true"));
    expect(dispatchWorkspace).toHaveBeenCalledWith("explore-thing", "initial brief", {
      acceptanceClauses: [],
      recordedClauses: [],
      counterCase: COUNTER_CASE,
      instructions: null,
      engineKey: undefined,
    });
  });

  it("commit() resolves false when dispatchWorkspace rejects", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.reject(new Error("dispatch failed")));
    render(createElement(Harness, { dispatchWorkspace }));
    await userEvent.click(screen.getByTestId("commit-btn"));
    await waitFor(() => expect(screen.getByTestId("commit-result")).toHaveTextContent("false"));
    expect(screen.getByTestId("dispatch-error")).toHaveTextContent("dispatch failed");
  });

  it("confirm surfaces a dispatchWorkspace rejection via the flow's own dispatchError, as a defensive fallback, visibly and not just in internal state", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.reject(new Error("dispatch failed")));
    render(createElement(Harness, { dispatchWorkspace }));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(screen.getByTestId("dispatch-error")).toHaveTextContent("dispatch failed"));
    expect(screen.getByTestId("dispatching")).toHaveTextContent("false");
    expect(screen.getByRole("alert")).toHaveTextContent("dispatch failed");
  });

  it("a fresh propose clears a stale dispatchError left over from a prior failed confirm", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.reject(new Error("dispatch failed")));
    const propose = vi.fn(() => Promise.resolve(readyResponse({ title: "fresh" })));
    render(createElement(Harness, { dispatchWorkspace, propose }));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("dispatch failed"));
    await userEvent.click(screen.getByTestId("propose-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the door's busy label on the idle button while the dispatch call is pending, restoring the idle label once it settles, with no status announcement", async () => {
    let resolveDispatch!: (ok: boolean) => void;
    const dispatchWorkspace = vi.fn(
      () => new Promise<boolean>((res) => { resolveDispatch = res; }),
    );
    render(createElement(Harness, { dispatchWorkspace }));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(screen.getByTestId(DOOR.idleTestId)).toBeDisabled());
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent("");
    resolveDispatch(true);
    await waitFor(() => expect(screen.getByTestId(DOOR.idleTestId)).not.toBeDisabled());
  });

  it("the door's idle button also reflects a busy workspace-level dispatch (workspaceDispatching)", () => {
    render(createElement(Harness, { workspaceDispatching: true }));
    expect(screen.getByTestId(DOOR.idleTestId)).toBeDisabled();
    expect(screen.getByTestId(DOOR.idleTestId)).toHaveTextContent(DOOR.pendingLabel);
  });

  it("surfaces a workspace-level dispatchError on the questions body", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Why?"])));
    render(createElement(Harness, { propose, workspaceDispatchError: "workspace dispatch failed" }));
    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));
    expect(screen.getByRole("alert")).toHaveTextContent("workspace dispatch failed");
  });

  it("a ready result clears any previously-surfaced open questions and answers", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Which database?"])));
    render(createElement(Harness, { propose }));
    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.getByTestId("questions-count")).toHaveTextContent("0");
  });

  it("a need_more_info result replaces a previously-ready proposal with the questions body", async () => {
    const propose = vi.fn(() => Promise.resolve(needMoreInfo(["Which database?"])));
    render(createElement(Harness, { propose }));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("propose-btn"));
    await waitFor(() => expect(screen.getByTestId("questions-count")).toHaveTextContent("1"));
    expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull();
  });

  it("confirm falls back to an empty title and empty acceptance clauses when the proposal omits them", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.resolve(true));
    render(
      createElement(Harness, {
        dispatchWorkspace,
        readyOverrides: { title: undefined, acceptance_clauses: undefined },
      }),
    );
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(dispatchWorkspace).toHaveBeenCalledTimes(1));
    expect(dispatchWorkspace).toHaveBeenCalledWith("", "initial brief", {
      acceptanceClauses: [],
      recordedClauses: [],
      counterCase: COUNTER_CASE,
      instructions: null,
      engineKey: undefined,
    });
  });

  it("confirm forwards the proposal's recorded clauses beside its acceptance clauses", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.resolve(true));
    render(
      createElement(Harness, {
        dispatchWorkspace,
        readyOverrides: { acceptance_clauses: ["it blocks"], recorded_clauses: ["the docs say X"] },
      }),
    );
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(dispatchWorkspace).toHaveBeenCalledTimes(1));
    expect(dispatchWorkspace).toHaveBeenCalledWith(
      "explore-thing",
      "initial brief",
      expect.objectContaining({
        acceptanceClauses: ["it blocks"],
        recordedClauses: ["the docs say X"],
      }),
    );
  });

  it("commit() called directly, as the auto-dispatch composer does, forwards recorded clauses too", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.resolve(true));
    render(
      createElement(Harness, {
        dispatchWorkspace,
        readyOverrides: { recorded_clauses: ["the docs say X"] },
      }),
    );
    await userEvent.click(screen.getByTestId("commit-btn"));
    await waitFor(() => expect(screen.getByTestId("commit-result")).toHaveTextContent("true"));
    expect(dispatchWorkspace).toHaveBeenCalledWith(
      "explore-thing",
      "initial brief",
      expect.objectContaining({ recordedClauses: ["the docs say X"] }),
    );
  });

  it("forwards a non-default engineKey into the dispatchWorkspace call", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.resolve(true));
    render(createElement(Harness, { dispatchWorkspace, engineKey: "acme/widget" }));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(dispatchWorkspace).toHaveBeenCalledTimes(1));
    expect(dispatchWorkspace).toHaveBeenCalledWith("explore-thing", "initial brief", {
      acceptanceClauses: [],
      recordedClauses: [],
      counterCase: COUNTER_CASE,
      instructions: null,
      engineKey: "acme/widget",
    });
  });

  it("passes non-default engineKey through to the rendered proposal fields", async () => {
    settleDefaultEngineKey(null);
    render(createElement(Harness, { engineKey: "acme/widget" }));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    expect(screen.getByTestId("engine-badge-dispatch-proposal")).toHaveTextContent("acme/widget");
  });

  it("omits the engine badge when engineKey is absent", async () => {
    render(createElement(Harness, {}));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    expect(screen.queryByTestId("engine-badge-dispatch-proposal")).toBeNull();
  });

  it("carries typed instructions from the panel's disclosure through to dispatchWorkspace", async () => {
    const dispatchWorkspace = vi.fn(() => Promise.resolve(true));
    render(createElement(Harness, { dispatchWorkspace }));
    await userEvent.click(screen.getByTestId("resolve-immediate-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
    const user = userEvent.setup();
    await user.click(screen.getByText("Instructions (optional)"));
    await user.type(screen.getByTestId("proposal-panel-instructions"), "Focus only on X.");
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(dispatchWorkspace).toHaveBeenCalledTimes(1));
    expect(dispatchWorkspace).toHaveBeenCalledWith(
      "explore-thing",
      "initial brief",
      expect.objectContaining({ instructions: "Focus only on X." }),
    );
  });
});

describe("useDispatchProposalFlow — no sessionId", () => {
  it("requestPropose is a no-op when sessionId is null", () => {
    const propose = vi.fn(() => Promise.resolve(readyResponse()));
    render(createElement(Harness, { sessionId: null, propose }));
    expect(propose).not.toHaveBeenCalled();
  });
});

describe("useDispatchProposalFlow — server-backed propose smoke test", () => {
  it("onDispatch refuses to propose when the live brief is blank", async () => {
    const propose = vi.fn(() => Promise.resolve(readyResponse()));
    render(createElement(Harness, { propose }));
    await userEvent.click(screen.getByTestId("blank-brief-btn"));
    await userEvent.click(screen.getByTestId("harness-idle"));
    expect(propose).not.toHaveBeenCalled();
  });

  it("exercises requestPropose against a real (mocked) HTTP call", async () => {
    server.use(
      http.post("/api/build-chat/sessions/sess-1/propose", () => HttpResponse.json(readyResponse())),
    );
    render(
      createElement(Harness, {
        propose: (sid) =>
          fetch(`/api/build-chat/sessions/${sid}/propose`, { method: "POST" }).then(
            (r) => r.json() as Promise<DispatchProposalResponse>,
          ),
      }),
    );
    await userEvent.click(screen.getByTestId("propose-btn"));
    await screen.findByTestId("dispatch-proposal-confirm");
  });
});
