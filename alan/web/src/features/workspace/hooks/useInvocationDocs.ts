import { useMemo } from "react";

import { api, type WorkflowDoc } from "../../../api";
import { useDocQuery } from "../../../api/queries";
import type { DocState } from "../components/InvocationCard";
import { errorMessageOf } from "../../../lib/errorMessage";

export interface DocsState {
  docs: ReadonlyMap<string, DocState | null>;
  errors: ReadonlyMap<string, string | null>;
  allSettled: boolean;
}

interface InvocationOutcome {
  invId: string;
  doc: DocState | null;
  error: string | null;
}

const EMPTY_DOCS_STATE: DocsState = { docs: new Map(), errors: new Map(), allSettled: false };

function docFromWorkflowDoc(d: WorkflowDoc): DocState {
  return {
    team: d.team,
    status: d.status,
    startedAt: d.started_at,
    completedAt: d.completed_at ?? null,
    sourceTaskId: d.source_task_id ?? null,
    tasks: d.tasks!,
  };
}

function fetchOutcome(taskId: string, invId: string): Promise<InvocationOutcome> {
  return api.getInvocation(taskId, invId).then(
    (d) => ({ invId, doc: docFromWorkflowDoc(d), error: null }),
    (err: unknown) => ({
      invId,
      doc: null,
      error: errorMessageOf(err),
    }),
  );
}

function settledState(outcomes: readonly InvocationOutcome[]): DocsState {
  const docs = new Map<string, DocState | null>();
  const errors = new Map<string, string | null>();
  for (const { invId, doc, error } of outcomes) {
    docs.set(invId, doc);
    errors.set(invId, error);
  }
  return { docs, errors, allSettled: true };
}

export function useInvocationDocs(taskId: string, invIds: readonly string[]): DocsState {
  const view = useDocQuery(
    "invocations",
    `${taskId}|${invIds.join(",")}`,
    () => Promise.all(invIds.map((invId) => fetchOutcome(taskId, invId))),
  );
  const outcomes = view.data;
  return useMemo(
    () => (outcomes === undefined ? EMPTY_DOCS_STATE : settledState(outcomes)),
    [outcomes],
  );
}
