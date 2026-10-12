import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  ApiError,
  api,
  type EnginePosture,
  type PreflightStatus,
  type Project as ApiProject,
  type WorkspaceSummary,
} from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import PostureConfirmModal from "../../../components/PostureConfirmModal";
import WizardFooter from "../components/WizardFooter";
import { useAction } from "../../../ui/useAction";
import {
  POSTURE_UNAVAILABLE_REASON,
  buildPostureRungs,
  hasPrBearingWorkspace,
  isSelectable,
  preflightRegister,
  selectReusableProject,
  type PostureRung,
  type PreflightRegister,
  type UnboundReason,
} from "./connectStep4PostureLogic";

export interface ConnectStep4PostureProps {
  engineKey: string;
}

interface Step4MountData {
  preflightOverall: PreflightStatus | null;
  currentPosture: EnginePosture;
  workspaces: WorkspaceSummary[];
  loading: boolean;
}

const INITIAL_MOUNT_DATA: Step4MountData = {
  preflightOverall: null,
  currentPosture: "observe",
  workspaces: [],
  loading: true,
};

// Fires api.runEnginePreflight + api.getEngine + api.listWorkspaces exactly
// once per engineKey. Guarded by a ref, not just the effect's dependency
// array: React.StrictMode double-invokes effects in dev, and
// runEnginePreflight is a real POST (persists a result, emits an event) a
// double-fire would needlessly re-run.
export function useStep4Mount(engineKey: string): Step4MountData {
  const [data, setData] = useState<Step4MountData>(INITIAL_MOUNT_DATA);
  const activeKey = useRef<string | null>(null);

  useEffect(() => {
    if (activeKey.current === engineKey) return;
    activeKey.current = engineKey;
    setData(INITIAL_MOUNT_DATA);
    void Promise.allSettled([
      api.runEnginePreflight(engineKey),
      api.getEngine(engineKey),
      api.listWorkspaces(),
    ]).then(([preflight, engine, workspaces]) => {
      // Stale-result guard: engineKey moved on before this settled.
      if (activeKey.current !== engineKey) return;
      setData({
        preflightOverall: preflight.status === "fulfilled" ? preflight.value.overall : null,
        currentPosture: engine.status === "fulfilled" ? engine.value.posture : "observe",
        workspaces: workspaces.status === "fulfilled" ? workspaces.value : [],
        loading: false,
      });
    });
  }, [engineKey]);

  return data;
}

const CHIP_CLASS: Record<PreflightRegister, string> = {
  "ready to dispatch": "bg-status-success-tint/15 text-status-success-fg",
  blocked: "bg-status-danger-tint/15 text-status-danger-fg",
  "partially ready": "bg-status-attention-tint/15 text-status-attention-fg",
};

function ReadinessChip({ overall }: Readonly<{ overall: PreflightStatus | null }>) {
  const register = preflightRegister(overall);
  return (
    <span
      data-testid="step4-readiness-chip"
      className={`inline-block rounded px-2 py-0.5 text-xs ${CHIP_CLASS[register]}`}
    >
      {register}
    </span>
  );
}

const UNAVAILABLE_REASON_ID = "posture-unavailable-reason";
const OPTION_ROW_CLASS =
  "flex min-h-11 cursor-pointer items-center gap-2 rounded-md px-2 py-1 focus-within:outline-none " +
  "focus-within:ring-2 focus-within:ring-text-primary/60";

interface PostureRadioRowProps {
  rung: PostureRung;
  checked: boolean;
  onSelect: (value: EnginePosture) => void;
}

// The caption renders as a sibling <p>, not nested inside <label>: jsx-a11y's
// label-has-associated-control only looks two JSX levels deep for accessible
// text, and a label -> wrapper span -> text span nesting sits one level past
// that budget.
function PostureRadioRow({ rung, checked, onSelect }: Readonly<PostureRadioRowProps>) {
  return (
    <div>
      <label className={OPTION_ROW_CLASS}>
        <input
          type="radio"
          name="posture"
          value={rung.value}
          checked={checked}
          onChange={() => onSelect(rung.value)}
          disabled={!rung.selectable}
          aria-describedby={rung.selectable ? undefined : UNAVAILABLE_REASON_ID}
          className="h-4 w-4 border-border disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid={`step4-posture-${rung.value}`}
        />
        <span className="text-sm text-text-primary">{rung.label}</span>
      </label>
      <p className="ml-6 text-xs text-text-muted">{rung.caption}</p>
    </div>
  );
}

interface PostureRadioGroupProps {
  rungs: readonly PostureRung[];
  selected: EnginePosture;
  onSelect: (value: EnginePosture) => void;
}

function PostureRadioGroup({ rungs, selected, onSelect }: Readonly<PostureRadioGroupProps>) {
  const hasUnavailableRung = rungs.some((rung) => !rung.selectable);
  return (
    <div className="space-y-2">
      <div role="radiogroup" aria-label="Operating posture" className="space-y-1">
        {rungs.map((rung) => (
          <PostureRadioRow
            key={rung.value}
            rung={rung}
            checked={selected === rung.value}
            onSelect={onSelect}
          />
        ))}
      </div>
      {hasUnavailableRung && (
        <p id={UNAVAILABLE_REASON_ID} role="note" className="text-xs text-text-muted">
          {POSTURE_UNAVAILABLE_REASON}
        </p>
      )}
    </div>
  );
}

// "titled from the repo" (docs/architecture/connect_wizard.md) — the repo
// segment only, not the owner/repo slug.
function repoTitleFrom(engineKey: string): string {
  return engineKey.slice(engineKey.indexOf("/") + 1);
}

async function ensureProject(engineKey: string, title: string): Promise<ApiProject> {
  const existing = await api.listProjects({ includeArchived: true });
  const match = selectReusableProject(existing, engineKey);
  if (match !== undefined) return match;
  return api.createProject(title, null, engineKey);
}

function unboundReasonForBindError(err: unknown): UnboundReason {
  return err instanceof ApiError && err.status === 409 ? "bind_conflict" : "bind_rejected";
}

// The safety gate this item must not bypass or re-implement:
// alan/routes/projects.py's _check_engine_rebind_gates 422s
// "engine preflight not passing" unless the persisted last preflight is
// overall === "pass" — so this only even attempts the bind when the client's
// own read of that same result says pass, and still handles the gate firing
// anyway (a race, or a 409 from a live workspace) rather than assuming it
// can't.
async function bindProjectIfPassing(
  projectId: string,
  engineKey: string,
  preflightOverall: PreflightStatus | null,
): Promise<UnboundReason | null> {
  if (preflightOverall === "fail") return "preflight_blocked";
  if (preflightOverall !== "pass") return "preflight_unknown";
  try {
    await api.updateProject(projectId, { engine_key: engineKey });
    return null;
  } catch (err) {
    return unboundReasonForBindError(err);
  }
}

export default function ConnectStep4Posture({ engineKey }: Readonly<ConnectStep4PostureProps>) {
  const navigate = useNavigate();
  const data = useStep4Mount(engineKey);
  const [selected, setSelected] = useState<EnginePosture>("observe");
  const [modalOpen, setModalOpen] = useState(false);
  const { busy: finishing, run } = useAction("rejection");
  const [finishError, setFinishError] = useState<string | null>(null);
  const userChangedPosture = useRef(false);

  useEffect(() => {
    userChangedPosture.current = false;
  }, [engineKey]);

  // While loading, pins the radio at "observe" — the pre-select DoD clause —
  // rather than showing a stale prior engine's value; once data.currentPosture
  // is known, mirrors it exactly, so a revisit after an earlier raise starts
  // the radio at the engine's real posture instead of a hardcoded observe
  // (which would otherwise make an untouched Finish propose a phantom
  // downgrade back to observe on that revisit). Never overrides a selection
  // the user has actually made — onSelect below is the only other writer of
  // `selected`, and it flips the guard the instant it fires.
  useEffect(() => {
    if (userChangedPosture.current) return;
    setSelected(data.loading ? "observe" : data.currentPosture);
  }, [engineKey, data.loading, data.currentPosture]);

  const selectPosture = (value: EnginePosture): void => {
    userChangedPosture.current = true;
    setSelected(value);
  };

  const rungs = buildPostureRungs(hasPrBearingWorkspace(data.workspaces, engineKey));

  const runProjectChain = (): void => {
    setFinishError(null);
    void run(async () => {
      const project = await ensureProject(engineKey, repoTitleFrom(engineKey));
      const unboundReason = await bindProjectIfPassing(
        project.id,
        engineKey,
        data.preflightOverall,
      );
      void navigate(`/projects/${project.id}`, {
        state: unboundReason === null ? undefined : { connectUnboundReason: unboundReason },
      });
    }).catch((err: unknown) => {
      setFinishError(errorMessageOf(err));
    });
  };

  const handleFinish = (): void => {
    // Second independent enforcement point (the third is structural: see
    // PostureConfirmModal's targetPosture prop below). `selected` can only
    // ever be set via a radio's onChange, which — verified empirically —
    // React never invokes for a disabled controlled input, even via a raw
    // DOM "change" event dispatched directly at the node; the browser-level
    // `disabled` attribute is not just decorative here. This re-check is
    // therefore unreachable through any test that respects that guarantee,
    // but stays as defense-in-depth against a future change desyncing
    // `selected` from `rungs`.
    /* v8 ignore next */
    if (!isSelectable(rungs, selected)) return;
    if (selected === data.currentPosture) {
      runProjectChain();
      return;
    }
    setModalOpen(true);
  };

  return (
    <div className="space-y-4" data-testid="connect-step4-posture">
      <div className="flex flex-wrap items-center gap-2">
        <ReadinessChip overall={data.preflightOverall} />
      </div>

      <PostureRadioGroup rungs={rungs} selected={selected} onSelect={selectPosture} />

      {finishError !== null && (
        <p role="alert" className="text-xs text-btn-destroy" data-testid="step4-finish-error">
          {finishError}
        </p>
      )}

      <WizardFooter
        primaryLabel="Finish"
        testId="step4-footer"
        caption="Saves your posture choice, creates the project and takes you to it."
        onPrimary={handleFinish}
        primaryDisabled={finishing || data.loading}
      />

      {modalOpen && (
        <PostureConfirmModal
          engineKey={engineKey}
          currentPosture={data.currentPosture}
          targetPosture={selected}
          onClose={() => {
            setModalOpen(false);
          }}
          onConfirmed={runProjectChain}
        />
      )}
    </div>
  );
}
