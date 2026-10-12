import { useCallback, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";

import type { PrepChatRef, WorkspaceCostBreakdownEntry, WorkspaceDetail } from "../../../api";
import HoverReveal from "../../../components/HoverReveal";
import { useConsultants } from "../../../hooks/useConsultants";
import { useDefaultEngineKey } from "../../../hooks/useEngines";
import { useMediaQuery, WIDE_SCREEN_QUERY } from "../../../hooks/useMediaQuery";
import { formatActiveDuration, formatCostUsd, formatDate } from "../../../lib/format";
import Disclosure from "../../../ui/Disclosure";
import StickyInstructionCard from "./StickyInstructionCard";
import WorkspaceCostBreakdown from "./WorkspaceCostBreakdown";
import WorkspaceNoteEditor from "./WorkspaceNoteEditor";

function Fact({
  label,
  value,
  testId,
}: Readonly<{ label: string; value: ReactNode; testId?: string }>) {
  return (
    <div className="flex items-baseline gap-2" data-testid={testId}>
      <p className="text-xs uppercase tracking-wide text-text-muted">{label}</p>
      <p className="text-sm tabular-nums text-text-primary">{value}</p>
    </div>
  );
}

const LEAD_TIME_NOTE = "calendar time from workspace creation to confirmed production delivery";

function leadTimeAriaLabel(compact: string, leadSeconds: number | null): string {
  if (leadSeconds === null) return `Lead time not yet recorded — ${LEAD_TIME_NOTE}`;
  return `Lead time ${compact} — ${LEAD_TIME_NOTE}`;
}

function WorkspaceLeadTime({ leadSeconds }: Readonly<{ leadSeconds: number | null }>) {
  const compact = formatActiveDuration(leadSeconds);
  return (
    <span data-testid="workspace-lead-time" aria-label={leadTimeAriaLabel(compact, leadSeconds)}>
      Lead: {compact}
    </span>
  );
}

interface WorkspaceCostSummaryProps {
  readonly totalCostUsd: number | null | undefined;
  readonly costBreakdown: WorkspaceCostBreakdownEntry[];
  readonly leadSeconds: number | null;
}

function WorkspaceCostSummary({
  totalCostUsd,
  costBreakdown,
  leadSeconds,
}: Readonly<WorkspaceCostSummaryProps>) {
  if (totalCostUsd == null && leadSeconds === null) return null;
  return (
    <div className="space-y-1 text-xs text-text-muted">
      <div className="flex flex-wrap gap-4 tabular-nums">
        {totalCostUsd != null && (
          <span data-testid="workspace-total-cost">Total: {formatCostUsd(totalCostUsd)}</span>
        )}
        <WorkspaceLeadTime leadSeconds={leadSeconds} />
      </div>
      {totalCostUsd != null && <WorkspaceCostBreakdown entries={costBreakdown} />}
    </div>
  );
}

function prepChatLinkLabel(
  prepChat: PrepChatRef,
  labelOf: (key: string | null) => string | undefined,
): string {
  if (prepChat.kind === "build") return "View preparation chat →";
  const consultantKey = prepChat.consultant_key ?? null;
  const label = labelOf(consultantKey) ?? consultantKey ?? "consultant";
  return `View ${label} conversation →`;
}

const PREP_CHAT_LINK_CLASS =
  "inline-flex min-h-11 items-center text-sm text-btn-action underline " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60";
const PREP_CHAT_LINK_DESCRIPTION = "Opens the conversation this workspace was set up from.";

function PrepChatLink({
  taskId,
  prepChat,
}: Readonly<{ taskId: string; prepChat: PrepChatRef | null | undefined }>) {
  const { labelOf } = useConsultants();
  const descriptionId = useId();
  if (prepChat == null) return null;
  return (
    <HoverReveal id={descriptionId} description={PREP_CHAT_LINK_DESCRIPTION}>
      <Link
        to={`/workspaces/${taskId}/prep-chat`}
        data-testid="prep-chat-link"
        className={PREP_CHAT_LINK_CLASS}
      >
        {prepChatLinkLabel(prepChat, labelOf)}
      </Link>
    </HoverReveal>
  );
}

function ProjectLink({
  projectId,
}: Readonly<{ projectId: string | null | undefined }>) {
  if (projectId == null) return null;
  return (
    <Link
      to={`/projects/${projectId}`}
      data-testid="project-link"
      className="inline-flex min-h-11 items-center text-sm text-btn-action underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
    >
      View project →
    </Link>
  );
}

interface WorkspaceDetailsContentProps {
  readonly ws: WorkspaceDetail;
  readonly taskId: string;
  readonly onInstructionCleared: () => void;
}

function WorkspaceDetailsContent({
  ws,
  taskId,
  onInstructionCleared,
}: Readonly<WorkspaceDetailsContentProps>) {
  const defaultEngineKey = useDefaultEngineKey();
  const repositoryKey = ws.engine_key ?? defaultEngineKey;
  return (
    <div className="space-y-4">
      {repositoryKey !== null && (
        <Fact label="Repository" value={repositoryKey} testId="workspace-repository" />
      )}
      <Fact label="Created" value={formatDate(ws.created_at)} testId="workspace-created" />
      <Fact
        label="Elapsed"
        value={formatActiveDuration(ws.active_seconds)}
        testId="workspace-elapsed"
      />
      <WorkspaceCostSummary
        totalCostUsd={ws.total_cost_usd}
        costBreakdown={ws.cost_breakdown ?? []}
        leadSeconds={ws.lead_seconds ?? null}
      />
      <PrepChatLink taskId={taskId} prepChat={ws.prep_chat} />
      <ProjectLink projectId={ws.project_id} />
      <StickyInstructionCard key={ws.updated_at} taskId={taskId} onCleared={onInstructionCleared} />
      <WorkspaceNoteEditor key={taskId} taskId={taskId} initialNote={ws.note} />
    </div>
  );
}

function usePersistentContentSlot(wide: boolean): {
  readonly setWrapper: (node: HTMLElement | null) => void;
  readonly slot: HTMLDivElement;
} {
  const [slot] = useState<HTMLDivElement>(() => document.createElement("div"));
  const wrapperRef = useRef<HTMLElement | null>(null);
  const setWrapper = useCallback((node: HTMLElement | null) => {
    wrapperRef.current = node;
  }, []);
  useLayoutEffect(() => {
    wrapperRef.current?.appendChild(slot);
  }, [wide, slot]);
  return { setWrapper, slot };
}

export interface WorkspaceDetailsAsideProps {
  readonly ws: WorkspaceDetail;
  readonly taskId: string;
  readonly onInstructionCleared: () => void;
}

export default function WorkspaceDetailsAside({
  ws,
  taskId,
  onInstructionCleared,
}: Readonly<WorkspaceDetailsAsideProps>) {
  const wide = useMediaQuery(WIDE_SCREEN_QUERY);
  const { setWrapper, slot } = usePersistentContentSlot(wide);

  const portaledContent = createPortal(
    <WorkspaceDetailsContent ws={ws} taskId={taskId} onInstructionCleared={onInstructionCleared} />,
    slot,
  );

  if (wide) {
    return (
      <>
        <aside className="space-y-4" data-testid="workspace-details-aside" ref={setWrapper} />
        {portaledContent}
      </>
    );
  }

  return (
    <>
      <Disclosure
        preset="panel"
        defaultOpen={false}
        testId="workspace-details-disclosure"
        label={<span>Details</span>}
      >
        <div className="mt-2" ref={setWrapper} />
      </Disclosure>
      {portaledContent}
    </>
  );
}
