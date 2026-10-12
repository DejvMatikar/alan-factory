import { useEffect, useId, useRef, useState } from "react";

import {
  api,
  type AgentPromptSegment,
  type LaunchRecord,
  type NodeConfigDoc,
  type NodeDetailTab,
  type NodeResultDoc,
} from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import { useModalLifecycle } from "../../../ui/useModalLifecycle";
import AgentPromptViewer from "../../../components/AgentPromptViewer";
import Button from "../../../components/Button";
import LaunchSetupList, {
  NOT_RECORDED_TEXT,
  notRecordedLaunchValues,
  recordLaunchValues,
} from "../../../components/LaunchSetupList";
import HoverReveal from "../../../components/HoverReveal";
import MarkdownPanel from "../../../components/MarkdownPanel";
import { handleModalKeyDown } from "../../../components/modalKeys";
import ShortcutHint from "../../../components/ShortcutHint";
import UnderlineTab from "../../../components/UnderlineTab";
import { handleTablistKeyDown } from "../../../components/tablistKeys";

export interface NodeDetailModalProps {
  taskId: string;
  invId: string;
  nodeId: string;
  stepLabel: string;
  initialTab: NodeDetailTab;
  onClose: () => void;
}

type TabState<T> =
  | { status: "idle"; data: null; error: null }
  | { status: "loading"; data: null; error: null }
  | { status: "error"; data: null; error: string }
  | { status: "ok"; data: T; error: null };

function initialTabState<T>(): TabState<T> {
  return { status: "idle", data: null, error: null };
}

const TAB_LABELS: Record<NodeDetailTab, string> = {
  output: "Output",
  prompt: "Prompt",
  config: "Config",
};

const TABS: readonly NodeDetailTab[] = ["output", "prompt", "config"];

const noop = (): void => undefined;

function CloseControl({ onClose }: Readonly<{ onClose: () => void }>) {
  const descriptionId = useId();
  return (
    <HoverReveal id={descriptionId} description="Close this pop-up" align="end">
      <Button
        variant="ghost"
        iconOnly
        aria-label="Close"
        aria-keyshortcuts="Escape"
        data-testid="node-detail-close"
        onClick={onClose}
      >
        <span aria-hidden="true">✕</span>
        <ShortcutHint glyph="Esc" spoken="Escape" />
      </Button>
    </HoverReveal>
  );
}

function OutputView({ state }: Readonly<{ state: TabState<NodeResultDoc> }>) {
  if (state.status === "idle" || state.status === "loading") {
    return <p className="text-xs text-text-muted" data-testid="output-loading">loading…</p>;
  }
  if (state.status === "error") {
    return <p className="text-xs text-red-400" data-testid="output-error">{state.error}</p>;
  }
  return (
    <MarkdownPanel
      title="Output"
      ariaLabel="Node output"
      testId="output-content"
      contentClassName="text-sm text-text-primary"
    >
      {state.data.result_text}
    </MarkdownPanel>
  );
}

function recordedSegments(record: LaunchRecord): AgentPromptSegment[] {
  return record.prompt_segments.map((segment) => ({ kind: "text", ...segment }));
}

function ProseBlock({ label, text, testId }: Readonly<{ label: string; text: string; testId: string }>) {
  return (
    <div>
      <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-text-muted">{label}</p>
      <pre
        className="whitespace-pre-wrap break-words rounded bg-surface px-3 py-2 text-xs text-text-primary"
        data-testid={testId}
      >
        {text}
      </pre>
    </div>
  );
}

function ResolvedSystemPrompt({ doc }: Readonly<{ doc: NodeConfigDoc }>) {
  if (doc.launch !== undefined) {
    return (
      <AgentPromptViewer
        segments={recordedSegments(doc.launch)}
        title="Resolved system prompt appended to Claude Code's own"
        testId="prompt-resolved"
      />
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-xs text-text-muted italic" data-testid="prompt-resolved-not-recorded">
        Resolved system prompt: {NOT_RECORDED_TEXT}
      </p>
      <ProseBlock
        label="System prompt (recorded when the step started)"
        text={doc.system_prompt}
        testId="prompt-recorded-system"
      />
    </div>
  );
}

function PromptView({ state }: Readonly<{ state: TabState<NodeConfigDoc> }>) {
  if (state.status === "idle" || state.status === "loading") {
    return <p className="text-xs text-text-muted" data-testid="prompt-loading">loading…</p>;
  }
  if (state.status === "error") {
    return <p className="text-xs text-red-400" data-testid="prompt-error">{state.error}</p>;
  }
  return (
    <div data-testid="prompt-content" className="space-y-3 overflow-y-auto">
      <ResolvedSystemPrompt doc={state.data} />
      <ProseBlock label="First user message" text={state.data.user_text} testId="prompt-user-text" />
    </div>
  );
}

function RecordedConfigRows({ doc }: Readonly<{ doc: NodeConfigDoc }>) {
  return (
    <dl className="space-y-2 text-xs" data-testid="config-recorded">
      {doc.launch === undefined && (
        <>
          <ConfigRow label="Model (recorded)" value={doc.model} />
          <ConfigRow label="Max turns (recorded)" value={String(doc.max_turns)} />
          <ConfigRow label="Permission mode (recorded)" value={doc.permission_mode} />
        </>
      )}
      {doc.turns_source === "adaptive" && (
        <ConfigRow
          testId="config-adaptive-ceiling"
          label="Adaptive ceiling"
          value={`derived ${doc.derived_max_turns}, applied ${doc.max_turns}`}
        />
      )}
      <ConfigRow label="Allowed tools (stored)" value={doc.allowed_tools.join(", ") || "(none)"} />
    </dl>
  );
}

function ConfigView({ state }: Readonly<{ state: TabState<NodeConfigDoc> }>) {
  if (state.status === "idle" || state.status === "loading") {
    return <p className="text-xs text-text-muted" data-testid="config-loading">loading…</p>;
  }
  if (state.status === "error") {
    return <p className="text-xs text-red-400" data-testid="config-error">{state.error}</p>;
  }
  const { launch } = state.data;
  return (
    <div data-testid="config-content" className="space-y-4 overflow-y-auto">
      <LaunchSetupList
        testId="config-launch"
        values={launch === undefined ? notRecordedLaunchValues() : recordLaunchValues(launch)}
      />
      <RecordedConfigRows doc={state.data} />
    </div>
  );
}

function ConfigRow({
  label,
  value,
  testId,
}: Readonly<{ label: string; value: string; testId?: string }>) {
  return (
    <div className="flex gap-2" data-testid={testId}>
      <dt className="w-32 shrink-0 text-text-muted">{label}</dt>
      <dd className="text-text-primary">{value}</dd>
    </div>
  );
}

export default function NodeDetailModal({
  taskId,
  invId,
  nodeId,
  stepLabel,
  initialTab,
  onClose,
}: Readonly<NodeDetailModalProps>) {
  const dialogRef = useModalLifecycle();
  const headingId = useId();
  const tablistRef = useRef<HTMLDivElement>(null);
  const [activeTab, setActiveTab] = useState<NodeDetailTab>(initialTab);
  const [resultState, setResultState] = useState<TabState<NodeResultDoc>>(initialTabState);
  const [configState, setConfigState] = useState<TabState<NodeConfigDoc>>(initialTabState);
  const fetchedTabs = useRef<Set<string>>(new Set());
  const lastIdentity = useRef<string>("");

  useEffect(() => {
    const identity = `${taskId}|${invId}|${nodeId}`;
    if (lastIdentity.current !== identity) {
      lastIdentity.current = identity;
      fetchedTabs.current = new Set();
      setResultState(initialTabState);
      setConfigState(initialTabState);
      tablistRef.current
        ?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
        ?.focus();
    }
    const fetchKey = activeTab === "output" ? "output" : "prompt_config";
    if (fetchedTabs.current.has(fetchKey)) return;

    if (activeTab === "output") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setResultState({ status: "loading", data: null, error: null });
      api.fetchNodeResult(taskId, invId, nodeId).then(
        (doc) => {
          fetchedTabs.current.add(fetchKey);
          setResultState({ status: "ok", data: doc, error: null });
        },
        (err: unknown) => setResultState({
          status: "error",
          data: null,
          error: errorMessageOf(err),
        }),
      );
    } else {
      setConfigState({ status: "loading", data: null, error: null });
      api.fetchNodeConfig(taskId, invId, nodeId).then(
        (doc) => {
          fetchedTabs.current.add(fetchKey);
          setConfigState({ status: "ok", data: doc, error: null });
        },
        (err: unknown) => setConfigState({
          status: "error",
          data: null,
          error: errorMessageOf(err),
        }),
      );
    }
  }, [activeTab, taskId, invId, nodeId]);

  return (
    <dialog
      ref={dialogRef}
      data-testid="node-detail-dialog"
      aria-labelledby={headingId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      className="fixed inset-0 m-auto h-[80dvh] max-w-2xl w-[90vw] rounded-lg p-0 border border-border bg-surface-elevated text-text-primary shadow-lg backdrop:bg-surface/80"
    >
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
      <div
        className="flex h-full flex-col p-5 gap-3"
        onKeyDown={(e) => {
          handleModalKeyDown(e, { onEscape: onClose, onCtrlEnter: noop });
        }}
      >
        <div className="flex items-center justify-between">
          <h2 id={headingId} className="text-xs font-medium text-text-muted font-mono">
            {stepLabel}
          </h2>
          <CloseControl onClose={onClose} />
        </div>
        <div
          ref={tablistRef}
          role="tablist"
          aria-label="Step details"
          tabIndex={-1}
          className="flex gap-4 border-b border-border"
          onKeyDown={handleTablistKeyDown}
        >
          {TABS.map((tab) => (
            <UnderlineTab
              key={tab}
              id={tab}
              label={TAB_LABELS[tab]}
              active={activeTab === tab}
              onClick={() => setActiveTab(tab)}
            />
          ))}
        </div>
        <div
          role="tabpanel"
          id={`tabpanel-${activeTab}`}
          aria-labelledby={`tab-${activeTab}`}
          className="flex-1 min-h-0 overflow-y-auto"
        >
          {activeTab === "output" && <OutputView state={resultState} />}
          {activeTab === "prompt" && <PromptView state={configState} />}
          {activeTab === "config" && <ConfigView state={configState} />}
        </div>
      </div>
    </dialog>
  );
}
