import { useCallback, useState } from "react";

import { api, ApiError, type EngineQuiesceReport, type Project } from "../../../api";
import {
  announceHiddenRepoChange,
  HIDDEN_REPO_DISCLOSURE_SUMMARY_ID,
} from "../../../components/HiddenRepoDialog";
import { errorMessageOf } from "../../../lib/errorMessage";

export type BindFailureReason = "preflight" | "live_workspaces" | "unknown_engine" | "other";

export type BindOutcome =
  | { readonly kind: "bound"; readonly project: Project }
  | { readonly kind: "unbound"; readonly project: Project; readonly reason: BindFailureReason; readonly detail: string };

export type MaintenanceOutcome =
  | { readonly kind: "running"; readonly taskId: string; readonly stopError?: string }
  | { readonly kind: "stopped"; readonly taskId: string }
  | { readonly kind: "error"; readonly detail: string };

function reasonFromStatus(status: number): BindFailureReason {
  if (status === 422) return "preflight";
  if (status === 409) return "live_workspaces";
  if (status === 400) return "unknown_engine";
  return "other";
}

function healthCheckTitle(): string {
  return `Health-check ${new Date().toISOString().slice(0, 19).replace("T", " ")}`;
}

// Honest reporting: a hide always takes effect (no new work can start against
// this repo from here on), but this sentence names exactly what refused to
// stop rather than implying a quiet that isn't real.
export function hideReportSentence(report: EngineQuiesceReport): string {
  const stillAlive: string[] = [];
  const residueCount = report.residue_workspace_ids!.length;
  if (residueCount > 0) {
    stillAlive.push(`${String(residueCount)} workspace${residueCount === 1 ? "" : "s"} still running`);
  }
  if (!report.pool_disposed) {
    stillAlive.push(report.pool_residue ?? "its warm pool not fully torn down");
  }
  if (stillAlive.length === 0) return "Hidden — everything for this repository stopped cleanly.";
  return `Hidden, but not everything stopped: ${stillAlive.join("; ")}.`;
}

export function hideReportTone(report: EngineQuiesceReport): "ok" | "warn" {
  if (report.residue_workspace_ids!.length > 0 || !report.pool_disposed) return "warn";
  return "ok";
}

export interface UseRepoActionsResult {
  readonly modalOpen: boolean;
  readonly openModal: () => void;
  readonly closeModal: () => void;
  readonly binding: boolean;
  readonly bindOutcome: BindOutcome | null;
  readonly onProjectCreated: (project: Project) => void;
  readonly maintenanceOutcome: MaintenanceOutcome | null;
  readonly maintenanceDispatching: boolean;
  readonly maintenanceStopping: boolean;
  readonly startMaintenance: () => void;
  readonly stopMaintenance: () => void;
  readonly hiding: boolean;
  readonly hideRepo: () => void;
}

export function useRepoActions(repoKey: string): UseRepoActionsResult {
  const [modalOpen, setModalOpen] = useState(false);
  const [binding, setBinding] = useState(false);
  const [bindOutcome, setBindOutcome] = useState<BindOutcome | null>(null);
  const [maintenanceOutcome, setMaintenanceOutcome] = useState<MaintenanceOutcome | null>(null);
  const [maintenanceDispatching, setMaintenanceDispatching] = useState(false);
  const [maintenanceStopping, setMaintenanceStopping] = useState(false);
  const [hiding, setHiding] = useState(false);

  const openModal = useCallback((): void => {
    setBindOutcome(null);
    setModalOpen(true);
  }, []);

  const closeModal = useCallback((): void => {
    setModalOpen(false);
  }, []);

  const onProjectCreated = useCallback(
    (project: Project): void => {
      setModalOpen(false);
      setBinding(true);
      api
        .updateProject(project.id, { engine_key: repoKey })
        .then(() => {
          setBindOutcome({ kind: "bound", project });
        })
        .catch((err: unknown) => {
          const reason = err instanceof ApiError ? reasonFromStatus(err.status) : "other";
          setBindOutcome({ kind: "unbound", project, reason, detail: errorMessageOf(err) });
        })
        .finally(() => {
          setBinding(false);
        });
    },
    [repoKey],
  );

  const startMaintenance = useCallback((): void => {
    setMaintenanceDispatching(true);
    api
      .createAndDispatchWorkspace(healthCheckTitle(), "", "maintenance", {}, { engineKey: repoKey })
      .then((result) => {
        setMaintenanceOutcome({ kind: "running", taskId: result.task_id });
      })
      .catch((err: unknown) => {
        setMaintenanceOutcome({ kind: "error", detail: errorMessageOf(err) });
      })
      .finally(() => {
        setMaintenanceDispatching(false);
      });
  }, [repoKey]);

  // Rewrites the outcome in place (running -> stopped) rather than clearing
  // it, so the notice's live region stays mounted and announces the new
  // text instead of a misclick silently disappearing. The POST itself is
  // fired from this callback body, not from inside the setState updater
  // above it — React.StrictMode double-invokes updater functions in dev, so
  // a real mutating request has to live outside one or a single Stop click
  // fires it twice. maintenanceStopping both disables the button while the
  // request is in flight and guards a rapid double-click from doing the same.
  // On rejection the outcome stays "running" with stopError attached, so the
  // maintenance run is never reported stopped when the server never
  // confirmed it — the Stop control stays mounted for a retry.
  const stopMaintenance = useCallback((): void => {
    if (maintenanceOutcome?.kind !== "running" || maintenanceStopping) return;
    const { taskId } = maintenanceOutcome;
    setMaintenanceStopping(true);
    api
      .stopWorkspace(taskId)
      .then(() => {
        setMaintenanceOutcome({ kind: "stopped", taskId });
      })
      .catch((err: unknown) => {
        setMaintenanceOutcome({ kind: "running", taskId, stopError: errorMessageOf(err) });
      })
      .finally(() => {
        setMaintenanceStopping(false);
      });
  }, [maintenanceOutcome, maintenanceStopping]);

  // The section this control lives in disappears from Home once the hide
  // takes effect (useEngines' visible-only `engines` filter drops it), which
  // would otherwise drop keyboard focus to <body> the moment the section
  // unmounts — hand focus to the always-mounted hidden-repos disclosure
  // right away instead, so the user lands where the repo now lives.
  //
  // The row's own notice can unmount before this promise even settles (the
  // hide route persists the hidden flag before doing its slow work, so an
  // engines refetch racing this request can drop the section first) — the
  // always-mounted announcement channel is therefore the only surface of
  // record for the outcome, on both the success and the failure path.
  const hideRepo = useCallback((): void => {
    setHiding(true);
    api
      .hideEngine(repoKey)
      .then((report) => {
        announceHiddenRepoChange({ text: hideReportSentence(report), tone: hideReportTone(report) });
        document.getElementById(HIDDEN_REPO_DISCLOSURE_SUMMARY_ID)?.focus();
      })
      .catch((err: unknown) => {
        announceHiddenRepoChange({ text: `Hide failed — ${errorMessageOf(err)}`, tone: "error" });
      })
      .finally(() => {
        setHiding(false);
      });
  }, [repoKey]);

  return {
    modalOpen,
    openModal,
    closeModal,
    binding,
    bindOutcome,
    onProjectCreated,
    maintenanceOutcome,
    maintenanceDispatching,
    maintenanceStopping,
    startMaintenance,
    stopMaintenance,
    hiding,
    hideRepo,
  };
}
