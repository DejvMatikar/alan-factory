import type { JSX } from "react";

import { api, type Handover, type WorkspaceStatus } from "../../../api";
import { useDocQuery } from "../../../api/queries";
import { formatDate } from "../../../lib/format";
import { errorMessageOf } from "../../../lib/errorMessage";
import MarkdownPanel from "../../../components/MarkdownPanel";
import { parseMarkdownSections } from "./retroSynthesisParser";

const RETRO_SECTIONS = [
  { key: "What worked", testId: "retro-what-worked" },
  { key: "What didn't", testId: "retro-what-didnt" },
  { key: "How to verify in the UI", testId: "retro-how-to-verify" },
] as const;

type HandoversState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "data"; handovers: Handover[] };

function handoversStateOf(
  handovers: Handover[] | undefined,
  isError: boolean,
  error: unknown,
): HandoversState {
  if (isError) return { kind: "error", message: errorMessageOf(error) };
  if (handovers === undefined) return { kind: "loading" };
  return { kind: "data", handovers };
}

function findRetrospectiveHandover(handovers: Handover[]): Handover | null {
  for (let i = handovers.length - 1; i >= 0; i--) {
    if (handovers[i]!.from_team === "retrospective") return handovers[i]!;
  }
  return null;
}

function retroSectionsOf(synthesis: string): (typeof RETRO_SECTIONS)[number][] {
  const sections = parseMarkdownSections(synthesis);
  return RETRO_SECTIONS.filter((s) => s.key in sections);
}

function deferredItemsOf(synthesis: string): string[] {
  const sections = parseMarkdownSections(synthesis);
  const body = sections["Action items"];
  if (!body) return [];
  const rawItems = body
    .split("\n")
    .map((line) => line.replace(/^(?:[-*]|\d+\.)\s*/, "").trim())
    .filter((line) => line.length > 0);
  return rawItems.filter((line, idx, arr) => arr.indexOf(line) === idx);
}

function retroSynthesisOf(state: HandoversState): string {
  if (state.kind !== "data") return "";
  return findRetrospectiveHandover(state.handovers)?.full_synthesis ?? "";
}

function hasDeliveryContent(liveAt: string | null | undefined, retroSynthesis: string): boolean {
  return (
    liveAt != null ||
    retroSectionsOf(retroSynthesis).length > 0 ||
    deferredItemsOf(retroSynthesis).length > 0
  );
}

function RetrospectiveSection({
  synthesis,
}: Readonly<{ synthesis: string }>): JSX.Element | null {
  const sections = parseMarkdownSections(synthesis);
  const present = retroSectionsOf(synthesis);
  if (present.length === 0) return null;
  return (
    <div data-testid="retrospective" className="space-y-2">
      <h3 className="text-sm font-medium">Retrospective</h3>
      {present.map((s) => (
        <MarkdownPanel
          key={s.key}
          title={s.key}
          ariaLabel={s.key}
          testId={s.testId}
          contentClassName="text-xs"
        >
          {sections[s.key]!}
        </MarkdownPanel>
      ))}
    </div>
  );
}

function DeferredSection({
  synthesis,
}: Readonly<{ synthesis: string }>): JSX.Element | null {
  const items = deferredItemsOf(synthesis);
  if (items.length === 0) return null;
  return (
    <div data-testid="deferred" className="space-y-2">
      <h3 className="text-sm font-medium">Deferred &amp; follow-ups</h3>
      <ul data-testid="deferred-items" className="ml-4 list-disc">
        {items.map((item, i) => (
          <li key={item} data-testid={`deferred-item-${i}`} className="text-xs">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function LiveSinceLine({
  liveAt,
}: Readonly<{ liveAt: string | null | undefined }>): JSX.Element | null {
  if (liveAt == null) return null;
  return (
    <p data-testid="live-since" className="text-xs text-text-muted">
      Live since <time dateTime={liveAt}>{formatDate(liveAt)}</time>
    </p>
  );
}

function DeliveryLoader({
  taskId,
  liveAt,
}: Readonly<{ taskId: string; liveAt: string | null | undefined }>): JSX.Element | null {
  const view = useDocQuery("workspace-retrospective", taskId, (id) => api.getHandovers(id));
  const state = handoversStateOf(view.data, view.isError, view.error);
  const retroSynthesis = retroSynthesisOf(state);

  if (state.kind === "data" && !hasDeliveryContent(liveAt, retroSynthesis)) return null;

  return (
    <section
      data-testid="delivery-summary-panel"
      aria-label="Delivery summary"
      className="space-y-4"
    >
      <LiveSinceLine liveAt={liveAt} />
      {state.kind === "loading" && (
        <p className="text-xs text-text-muted" data-testid="delivery-loading">
          Loading delivery summary…
        </p>
      )}
      {state.kind === "error" && (
        <div role="alert" data-testid="delivery-error">
          Failed to load retrospective: {state.message}
        </div>
      )}
      {state.kind === "data" && (
        <>
          <RetrospectiveSection synthesis={retroSynthesis} />
          <DeferredSection synthesis={retroSynthesis} />
        </>
      )}
    </section>
  );
}

export default function DeliverySummaryPanel({
  taskId,
  status,
  liveAt,
}: Readonly<{
  taskId: string;
  status: WorkspaceStatus;
  liveAt: string | null | undefined;
}>): JSX.Element | null {
  if (status !== "completed") return null;
  return <DeliveryLoader key={taskId} taskId={taskId} liveAt={liveAt} />;
}
