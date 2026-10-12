import { useRef, useState, type ReactNode } from "react";

import { flowErrorOf, type PendingReadyProposal, type ProposalFlowState } from "../hooks/proposalFlowReducer";
import ErrorBanner from "../../../ui/ErrorBanner";
import Input from "../../../ui/Input";
import Button from "../../../components/Button";
import DispatchProposalFields, { counterCaseDescribedBy } from "./DispatchProposalFields";
import QuestionsTab from "./QuestionsTab";

export interface ProposalPanelDoorConfig {
  idleTestId: string;
  idleLabel: string;
  pendingLabel: string;
  questionsTestId: string;
  questionsActionLabel: string;
  questionsBusyLabel: string;
}

export interface ProposalPanelProps {
  state: ProposalFlowState;
  liveBrief: string;
  engineKey?: string | null | undefined;
  roadmapItemLabel?: string | null | undefined;
  dispatching: boolean;
  pageDispatching: boolean;
  pageDispatchError: string | null;
  door: ProposalPanelDoorConfig;
  onAnswer: (i: number, value: string) => void;
  onSubmitAnswers: () => void;
  onDispatch: () => void;
  onConfirm: (ready: PendingReadyProposal, instructions: string | null) => void | Promise<void>;
}

function statusText(state: ProposalFlowState, stale: boolean, door: ProposalPanelDoorConfig): string {
  switch (state.kind) {
    case "proposing":
      return door.pendingLabel;
    case "propose_failed":
      return "Could not prepare the dispatch proposal.";
    case "questions":
      return "More information is needed before dispatching.";
    case "ready":
      return stale
        ? "Brief changed since this proposal — re-propose to see the refreshed one."
        : "Proposal ready to dispatch.";
    default:
      return "";
  }
}

interface ProposalPanelBodyProps {
  state: ProposalFlowState;
  engineKey?: string | null | undefined;
  roadmapItemLabel?: string | null | undefined;
  onAnswer: (i: number, value: string) => void;
  onSubmitAnswers: () => void;
  busy: boolean;
  pageDispatchError: string | null;
  door: ProposalPanelDoorConfig;
}

function ProposalPanelBody({
  state,
  engineKey,
  roadmapItemLabel,
  onAnswer,
  onSubmitAnswers,
  busy,
  pageDispatchError,
  door,
}: Readonly<ProposalPanelBodyProps>) {
  if (state.kind === "ready") {
    return (
      <DispatchProposalFields
        proposal={state.ready.proposal}
        engineKey={engineKey}
        roadmapItemLabel={roadmapItemLabel}
      />
    );
  }
  if (state.kind === "questions") {
    return (
      <QuestionsTab
        questions={state.questions}
        answers={state.answers}
        onAnswer={onAnswer}
        onDispatch={onSubmitAnswers}
        dispatching={state.submit.kind === "submitting" || busy}
        error={flowErrorOf(state) ?? pageDispatchError}
        actionLabel={door.questionsActionLabel}
        busyLabel={door.questionsBusyLabel}
        testId={door.questionsTestId}
      />
    );
  }
  if (state.kind === "propose_failed") {
    return <ErrorBanner>{state.error}</ErrorBanner>;
  }
  if (state.kind === "proposing") {
    return (
      <p className="text-sm text-text-muted" data-testid="proposal-panel-pending">
        {door.pendingLabel}
      </p>
    );
  }
  return null;
}

interface PrimaryButtonSpec {
  testId: string;
  label: ReactNode;
  disabled: boolean;
  stale: boolean;
  onClick: () => void;
  describedBy: string | undefined;
}

function primaryButtonSpec(
  state: ProposalFlowState,
  stale: boolean,
  door: ProposalPanelDoorConfig,
  busy: boolean,
  onDispatch: () => void,
  onConfirmClick: (ready: PendingReadyProposal) => void,
): PrimaryButtonSpec | null {
  if (state.kind === "questions") return null;
  if (state.kind === "ready" && stale) {
    return {
      testId: "dispatch-proposal-repropose",
      label: "Re-propose",
      disabled: false,
      stale: true,
      onClick: onDispatch,
      describedBy: undefined,
    };
  }
  if (state.kind === "ready") {
    return {
      testId: "dispatch-proposal-confirm",
      label: busy ? "Dispatching…" : "Dispatch",
      disabled: busy,
      stale: false,
      onClick: () => onConfirmClick(state.ready),
      describedBy: counterCaseDescribedBy(state.ready.proposal.counter_case),
    };
  }
  const busyIdle = state.kind === "proposing" || busy;
  return {
    testId: door.idleTestId,
    label: busyIdle ? door.pendingLabel : door.idleLabel,
    disabled: busyIdle,
    stale: false,
    onClick: onDispatch,
    describedBy: undefined,
  };
}

function trailingBannerError(state: ProposalFlowState, pageDispatchError: string | null): string | null {
  if (state.kind === "questions" || state.kind === "propose_failed") return null;
  return flowErrorOf(state) ?? pageDispatchError;
}

export default function ProposalPanel({
  state,
  liveBrief,
  engineKey,
  roadmapItemLabel,
  dispatching,
  pageDispatching,
  pageDispatchError,
  door,
  onAnswer,
  onSubmitAnswers,
  onDispatch,
  onConfirm,
}: Readonly<ProposalPanelProps>) {
  const outputRef = useRef<HTMLOutputElement>(null);
  const [instructions, setInstructions] = useState("");

  const busy = dispatching || pageDispatching;
  const stale = state.kind === "ready" && state.ready.briefContent !== liveBrief;
  const handleConfirmClick = (ready: PendingReadyProposal): void => {
    const trimmed = instructions.trim();
    void onConfirm(ready, trimmed === "" ? null : trimmed);
  };
  const button = primaryButtonSpec(state, stale, door, busy, onDispatch, handleConfirmClick);
  const bannerError = trailingBannerError(state, pageDispatchError);

  return (
    <div
      data-testid="proposal-panel"
      className={
        stale
          ? "space-y-3 rounded-md border border-border/40 bg-surface-elevated/40 p-3"
          : "space-y-3 rounded-md border border-border bg-surface-elevated p-3"
      }
    >
      <output
        ref={outputRef}
        tabIndex={-1}
        aria-live="polite"
        data-testid="proposal-panel-status"
        className="block text-xs text-text-muted"
      >
        {statusText(state, stale, door)}
      </output>
      <ProposalPanelBody
        state={state}
        engineKey={engineKey}
        roadmapItemLabel={roadmapItemLabel}
        onAnswer={onAnswer}
        onSubmitAnswers={onSubmitAnswers}
        busy={busy}
        pageDispatchError={pageDispatchError}
        door={door}
      />
      {bannerError !== null && <ErrorBanner>{bannerError}</ErrorBanner>}
      {button !== null && (
        <>
          <details>
            <summary className="cursor-pointer text-xs font-medium text-text-muted">
              Instructions (optional)
            </summary>
            <Input
              as="textarea"
              data-testid="proposal-panel-instructions"
              aria-label="Instructions (optional)"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={3}
              placeholder="Focus / context the dispatched team should honor."
              className="mt-1"
              placeholderStyle
              disabledStyle={false}
            />
          </details>
          <Button
            variant="action"
            data-testid={button.testId}
            disabled={button.disabled}
            onClick={button.onClick}
            aria-describedby={button.describedBy}
            className={button.stale ? "opacity-70" : ""}
          >
            {button.label}
          </Button>
        </>
      )}
    </div>
  );
}
