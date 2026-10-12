import { useState } from "react";
import type { ReactNode } from "react";

import {
  DependencySweepBlockedError,
  api,
  type DependencySweepStatus,
  type EnginePosture,
  type EnginePreflightFinding,
  type EnginePreflightResult,
  type PreflightStatus,
} from "../api";
import { errorMessageOf } from "../lib/errorMessage";
import { formatDate, formatRelativeTime, formatWarmupDuration } from "../lib/format";
import { useHealthContext } from "../api/HealthProvider";
import SectionHeading from "../ui/SectionHeading";
import { useEngineResource } from "../hooks/useEngineResource";
import { DISPATCHABLE_STEP_KEY } from "../lib/wizardStepSelection";
import Button from "./Button";
import FindingRow from "./FindingRow";
import PostureConfirmModal from "./PostureConfirmModal";
import { RefreshErrorLine, RefreshingLabel, refreshFlags } from "./RefreshCue";

export const POSTURE_LABEL: Record<EnginePosture, string> = {
  observe: "Observe",
  propose: "Propose",
  self_merge: "Self-merge",
  autonomous: "Autonomous",
};

export const POSTURE_STYLE: Record<EnginePosture, string> = {
  observe: "bg-zinc-500/15 text-zinc-300",
  propose: "bg-amber-500/15 text-amber-300",
  self_merge: "bg-blue-500/15 text-blue-300",
  autonomous: "bg-green-500/15 text-green-300",
};

export const PREFLIGHT_STYLE: Record<PreflightStatus, string> = {
  pass: "bg-green-500/15 text-green-300",
  fail: "bg-red-500/15 text-red-300",
  unknown: "bg-zinc-500/15 text-zinc-300",
};

const READINESS_CHECK = "dispatch_readiness";

const READINESS_LABEL: Record<PreflightStatus, string> = {
  pass: "Dispatch ready",
  fail: "Dispatch held",
  unknown: "Dispatch readiness unverified",
};

const _CI_DEPLOY_KINDS: ReadonlySet<string> = new Set(["main_ci", "deploy"]);

interface TileShellProps {
  readonly title: string;
  readonly testId: string;
  readonly refreshing?: boolean;
  readonly refreshError?: string | null;
  readonly children: ReactNode;
}

function TileShell({
  title,
  testId,
  refreshing = false,
  refreshError = null,
  children,
}: Readonly<TileShellProps>) {
  return (
    <div
      data-testid={testId}
      aria-busy={refreshing}
      className="rounded-lg border border-border bg-surface-elevated px-4 py-3"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionHeading level="h4" truncate>
          {title}
        </SectionHeading>
        {refreshing && <RefreshingLabel testId={`${testId}-refreshing`} />}
      </div>
      {refreshError !== null && (
        <RefreshErrorLine testId={`${testId}-refresh-error`} message={refreshError} />
      )}
      {children}
    </div>
  );
}

function TileLoading({ testId }: Readonly<{ testId: string }>) {
  return (
    <p className="text-sm text-text-muted" data-testid={testId}>
      Loading…
    </p>
  );
}

function TileError({ testId, message }: Readonly<{ testId: string; message: string }>) {
  return (
    <p role="alert" className="text-sm text-btn-destroy" data-testid={testId}>
      {message}
    </p>
  );
}

export function PostureTile({ engineKey }: Readonly<{ engineKey: string }>) {
  const { state } = useEngineResource("engineConfig", engineKey);
  const { refreshing, refreshError } = refreshFlags(state);
  const [modalPosture, setModalPosture] = useState<EnginePosture | null>(null);

  return (
    <TileShell
      title="Posture"
      testId="engine-ops-posture-tile"
      refreshing={refreshing}
      refreshError={refreshError}
    >
      {state.status === "loading" && <TileLoading testId="engine-ops-posture-loading" />}
      {state.status === "error" && (
        <TileError testId="engine-ops-posture-error" message={state.message} />
      )}
      {state.status === "ready" && (
        <div className="space-y-2">
          <p
            data-testid="engine-ops-identity"
            className="inline-block rounded px-2 py-0.5 text-xs bg-purple-500/15 text-purple-300"
          >
            {engineKey}
          </p>
          <div>
            <span
              data-testid="engine-ops-posture-pill"
              className={`inline-block rounded px-2 py-0.5 text-xs ${POSTURE_STYLE[state.data.posture]}`}
            >
              {POSTURE_LABEL[state.data.posture]}
            </span>
          </div>
          <Button
            variant="action"
            data-testid="engine-ops-posture-change-button"
            onClick={() => {
              setModalPosture(state.data.posture);
            }}
          >
            Change posture
          </Button>
          {modalPosture !== null && (
            <PostureConfirmModal
              engineKey={engineKey}
              currentPosture={modalPosture}
              onClose={() => {
                setModalPosture(null);
              }}
            />
          )}
        </div>
      )}
    </TileShell>
  );
}

function PoolTile({ engineKey }: Readonly<{ engineKey: string }>) {
  const { state } = useEngineResource("pool", engineKey);
  const { refreshing, refreshError } = refreshFlags(state);
  return (
    <TileShell
      title="Worker pool"
      testId="engine-ops-pool-tile"
      refreshing={refreshing}
      refreshError={refreshError}
    >
      {state.status === "loading" && <TileLoading testId="engine-ops-pool-loading" />}
      {state.status === "error" && (
        <TileError testId="engine-ops-pool-error" message={state.message} />
      )}
      {state.status === "ready" && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <span
              data-testid="engine-ops-pool-warm"
              className={`inline-block rounded px-2 py-0.5 text-xs ${
                state.data.warm ? "bg-green-500/15 text-green-300" : "bg-zinc-500/15 text-zinc-300"
              }`}
            >
              {state.data.warm ? "Warm" : "Cold"}
            </span>
            <span className="text-sm text-text-primary" data-testid="engine-ops-pool-free-count">
              {state.data.free_count} free
            </span>
            <span className="text-sm text-text-muted" data-testid="engine-ops-pool-leased-count">
              {state.data.leased_count} leased
            </span>
          </div>
          <p className="text-sm text-text-muted" data-testid="engine-ops-pool-warmup">
            {state.data.warmup_seconds == null
              ? "Not measured yet"
              : `Warm-up ${formatWarmupDuration(state.data.warmup_seconds)}`}
          </p>
        </div>
      )}
    </TileShell>
  );
}

function ReadinessRow({ finding }: Readonly<{ finding: EnginePreflightFinding }>) {
  return (
    <div className="space-y-0.5" data-testid="engine-ops-preflight-readiness">
      <span
        data-testid="engine-ops-preflight-readiness-status"
        className={`inline-block rounded px-2 py-0.5 text-xs ${PREFLIGHT_STYLE[finding.status]}`}
      >
        {READINESS_LABEL[finding.status]}
      </span>
      {(finding.status !== "pass" || finding.remedy !== "") && (
        <p className="text-xs text-text-muted" data-testid="engine-ops-preflight-readiness-message">
          {finding.message}
          {finding.remedy !== "" && ` Fix: ${finding.remedy}`}
        </p>
      )}
    </div>
  );
}

function PreflightResultBody({ result }: Readonly<{ result: EnginePreflightResult }>) {
  const readiness = result.findings.find((f) => f.check === READINESS_CHECK);
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-start gap-2">
        <span
          data-testid="engine-ops-preflight-outcome"
          className={`inline-block rounded px-2 py-0.5 text-xs ${PREFLIGHT_STYLE[result.overall]}`}
        >
          {result.overall}
        </span>
        {readiness !== undefined && <ReadinessRow finding={readiness} />}
      </div>
      <p
        title={new Date(result.checked_at).toLocaleString()}
        className="text-xs text-text-muted"
        data-testid="engine-ops-preflight-timing"
      >
        {formatRelativeTime(result.checked_at)}
      </p>
    </div>
  );
}

function PreflightTile({ engineKey }: Readonly<{ engineKey: string }>) {
  const { state } = useEngineResource("preflight", engineKey);
  const { refreshing, refreshError } = refreshFlags(state);
  return (
    <TileShell
      title="Last preflight"
      testId="engine-ops-preflight-tile"
      refreshing={refreshing}
      refreshError={refreshError}
    >
      {state.status === "loading" && <TileLoading testId="engine-ops-preflight-loading" />}
      {state.status === "error" && (
        <TileError testId="engine-ops-preflight-error" message={state.message} />
      )}
      {state.status === "ready" && state.data === null && (
        <p className="text-sm text-text-muted" data-testid="engine-ops-preflight-never-run">
          Never run
        </p>
      )}
      {state.status === "ready" && state.data !== null && (
        <PreflightResultBody result={state.data} />
      )}
    </TileShell>
  );
}

function DependencySweepStatusPill({ enabled }: Readonly<{ enabled: boolean }>) {
  return (
    <span
      data-testid="engine-ops-dep-sweep-status"
      className={`inline-block rounded px-2 py-0.5 text-xs ${
        enabled ? "bg-green-500/15 text-green-300" : "bg-zinc-500/15 text-zinc-300"
      }`}
    >
      {enabled ? "Opted in" : "Opted out"}
    </span>
  );
}

function DependencySweepRunTiming({
  status,
}: Readonly<{ status: DependencySweepStatus }>) {
  return (
    <>
      <p className="text-xs text-text-muted" data-testid="engine-ops-dep-sweep-last-run">
        {status.last_run_at === null ? (
          "Never run"
        ) : (
          <span title={new Date(status.last_run_at).toLocaleString()}>
            Last run {formatRelativeTime(status.last_run_at)}
          </span>
        )}
      </p>
      <p className="text-xs text-text-muted" data-testid="engine-ops-dep-sweep-next-run">
        {status.next_run_at === null ? (
          "Not scheduled"
        ) : (
          <span title={new Date(status.next_run_at).toLocaleString()}>
            Next run {formatDate(status.next_run_at)}
          </span>
        )}
      </p>
    </>
  );
}

function useDependencySweepToggle(
  engineKey: string,
): {
  toggling: boolean;
  toggleError: string | null;
  finding: EnginePreflightFinding | null;
  toggle: (currentlyEnabled: boolean) => void;
} {
  const [finding, setFinding] = useState<EnginePreflightFinding | null>(null);
  const [toggling, setToggling] = useState(false);
  const [toggleError, setToggleError] = useState<string | null>(null);

  const toggle = (currentlyEnabled: boolean): void => {
    setToggling(true);
    setToggleError(null);
    setFinding(null);
    api.setEngineDependencySweepEnabled(engineKey, !currentlyEnabled).then(
      () => {
        setToggling(false);
      },
      (err: unknown) => {
        setToggling(false);
        if (err instanceof DependencySweepBlockedError) {
          setFinding(err.finding);
          return;
        }
        setToggleError(errorMessageOf(err));
      },
    );
  };

  return { toggling, toggleError, finding, toggle };
}

export function DependencySweepTile({ engineKey }: Readonly<{ engineKey: string }>) {
  const { state } = useEngineResource("depSweep", engineKey);
  const { toggling, toggleError, finding, toggle } = useDependencySweepToggle(engineKey);
  const { refreshing, refreshError } = refreshFlags(state);

  return (
    <TileShell
      title="Dependency sweep"
      testId="engine-ops-dep-sweep-tile"
      refreshing={refreshing}
      refreshError={refreshError}
    >
      {state.status === "loading" && <TileLoading testId="engine-ops-dep-sweep-loading" />}
      {state.status === "error" && (
        <TileError testId="engine-ops-dep-sweep-error" message={state.message} />
      )}
      {state.status === "ready" && (
        <div className="space-y-2">
          <DependencySweepStatusPill enabled={state.data.enabled} />
          <DependencySweepRunTiming status={state.data} />
          {finding !== null && (
            <FindingRow finding={finding} testId="engine-ops-dep-sweep-finding" />
          )}
          <Button
            variant="action"
            data-testid="engine-ops-dep-sweep-toggle-button"
            onClick={() => {
              toggle(state.data.enabled);
            }}
            disabled={toggling}
          >
            {state.data.enabled ? "Opt out" : "Opt in"}
          </Button>
          {toggleError !== null && (
            <p
              role="alert"
              data-testid="engine-ops-dep-sweep-toggle-error"
              className="text-sm text-btn-destroy"
            >
              {toggleError}
            </p>
          )}
        </div>
      )}
    </TileShell>
  );
}

const ONBOARDING_OVERALL_LABEL: Record<PreflightStatus, string> = {
  pass: "Ready to dispatch",
  fail: "Blocked",
  unknown: "Partially ready",
};

// engineKey is always "owner/repo" (EngineRepoIdentity.slug shape) — split for
// the setup-step deep link, which is override-only per wizardStepSelection.ts.
// Fails fast on a malformed key instead of emitting a corrupted /connect URL,
// mirroring api.ts's internal engineOwnerRepo().
function engineKeyParts(engineKey: string): { owner: string; repo: string } {
  const idx = engineKey.indexOf("/");
  if (idx === -1) throw new Error(`invalid engineKey: ${engineKey}`);
  return { owner: engineKey.slice(0, idx), repo: engineKey.slice(idx + 1) };
}

function OnboardingTile({ engineKey }: Readonly<{ engineKey: string }>) {
  const { state } = useEngineResource("onboarding", engineKey);
  const { owner, repo } = engineKeyParts(engineKey);
  const { refreshing, refreshError } = refreshFlags(state);

  return (
    <TileShell
      title="Repo readiness"
      testId="engine-ops-onboarding-tile"
      refreshing={refreshing}
      refreshError={refreshError}
    >
      {state.status === "loading" && <TileLoading testId="engine-ops-onboarding-loading" />}
      {state.status === "error" && (
        <TileError testId="engine-ops-onboarding-error" message={state.message} />
      )}
      {state.status === "ready" && (
        <div className="space-y-2">
          <span
            data-testid="engine-ops-onboarding-overall"
            className={`inline-block rounded px-2 py-0.5 text-xs ${PREFLIGHT_STYLE[state.data.overall]}`}
          >
            {ONBOARDING_OVERALL_LABEL[state.data.overall]}
          </span>
          <Button
            variant="action"
            linkTo={`/connect/${owner}/${repo}/${DISPATCHABLE_STEP_KEY}`}
            data-testid="engine-ops-onboarding-setup-link"
          >
            Open setup →
          </Button>
        </div>
      )}
    </TileShell>
  );
}

function CiDeployTile({ engineKey }: Readonly<{ engineKey: string }>) {
  const { healthState, lastComputedAt } = useHealthContext();
  const findings = (healthState?.findings ?? []).filter(
    (f) => _CI_DEPLOY_KINDS.has(f.kind) && f.engine_key === engineKey,
  );
  return (
    <TileShell title="CI / deploy health" testId="engine-ops-ci-deploy-tile">
      {lastComputedAt !== null && (
        <p
          title={new Date(lastComputedAt).toLocaleString()}
          className="mb-2 text-xs text-text-muted"
          data-testid="engine-ops-ci-deploy-as-of"
        >
          As of {formatRelativeTime(lastComputedAt)}
        </p>
      )}
      {findings.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid="engine-ops-ci-deploy-healthy">
          No CI/deploy issues.
        </p>
      ) : (
        <ul className="space-y-1" data-testid="engine-ops-ci-deploy-findings">
          {findings.map((f) => (
            <li
              key={f.finding_id}
              className="text-sm text-text-primary"
              data-testid={`engine-ops-ci-deploy-finding-${f.finding_id}`}
            >
              {f.description}
            </li>
          ))}
        </ul>
      )}
    </TileShell>
  );
}

export interface EngineOpsPanelProps {
  engineKey: string;
}

export default function EngineOpsPanel({ engineKey }: Readonly<EngineOpsPanelProps>) {
  const { owner, repo } = engineKeyParts(engineKey);
  return (
    <section className="space-y-3" data-testid="engine-ops-panel">
      <h3 className="text-sm font-medium text-text-primary">Ops</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <PostureTile engineKey={engineKey} />
        <PoolTile engineKey={engineKey} />
        <PreflightTile engineKey={engineKey} />
        <DependencySweepTile engineKey={engineKey} />
        <OnboardingTile engineKey={engineKey} />
        <CiDeployTile engineKey={engineKey} />
      </div>
      <Button
        variant="action"
        linkTo={`/repos/${owner}/${repo}/config`}
        data-testid="engine-ops-configure-button"
      >
        Configure engine
      </Button>
    </section>
  );
}
