import { useState } from "react";

import { api } from "../../../api";
import type {
  DriverLoopDecision,
  DriverLoopHoldReason,
  DriverLoopOutcome,
  DriverLoopStatus,
} from "../../../api";
import Button from "../../../components/Button";
import HoverReveal from "../../../components/HoverReveal";
import { errorMessageOf } from "../../../lib/errorMessage";
import SectionHeading from "../../../ui/SectionHeading";
import { useControlTokenPresent } from "../../../hooks/useControlTokenPresent";
import AggregateChip, { type ChipTone } from "../../../components/AggregateChip";
import { SUBDUED_WEIGHT_CLASS } from "../../../components/SubduedActionControl";
import { useDriverLoops } from "../hooks/useDriverLoops";

const _HOLD_LABEL: Record<DriverLoopHoldReason, string> = {
  not_supervised: "Not supervised here",
  disabled: "Disabled",
  running: "Running",
  paused: "Paused",
  usage_pause: "Held — usage pause",
  interactive_lock: "Held — interactive session",
  fast_forward_failed: "Held — fast-forward failed",
  backoff: "Backing off",
};

const _HOLD_TONE: Record<string, ChipTone> = {
  interactive_lock: "red",
  fast_forward_failed: "red",
  backoff: "red",
  running: "transitional",
};

const _OUTCOME_LABEL: Record<DriverLoopOutcome, string> = {
  succeeded: "Succeeded",
  failed: "Failed",
  timed_out: "Timed out",
  held_usage_pause: "Held by usage pause",
  ran_with_denials: "Ran with denials",
  unknown_after_restart: "Unknown after restart",
};

export function loopStateTone(decision: DriverLoopDecision): ChipTone {
  if (decision.action === "spawn") return "transitional";
  if (decision.action === "idle") return "green";
  if (decision.reason === null) return "unknown";
  return _HOLD_TONE[decision.reason] ?? "unknown";
}

export function loopStateText(decision: DriverLoopDecision): string {
  if (decision.action === "spawn") return "Due now";
  if (decision.action === "idle") return "Idle";
  if (decision.reason === null) return "Held";
  return _HOLD_LABEL[decision.reason];
}

function outcomeLabel(outcome: DriverLoopOutcome | null): string {
  if (outcome === null) return "No runs yet";
  return _OUTCOME_LABEL[outcome];
}

function formatNextWake(nextWakeAt: number | null): string {
  if (nextWakeAt === null) return "—";
  return new Date(nextWakeAt * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function LoadingState() {
  return (
    <p className="text-xs text-text-muted" data-testid="driver-loops-loading">
      Driver loops — loading…
    </p>
  );
}

function ErrorState() {
  return (
    <p className="text-xs text-text-muted" data-testid="driver-loops-error">
      Driver loop status unavailable.
    </p>
  );
}

function EmptyState({ supervised }: Readonly<{ supervised: boolean }>) {
  return (
    <p className="text-xs text-text-muted" data-testid="driver-loops-empty">
      {supervised
        ? "No driver loops configured. The drivers run interactively today; enabling a headless loop waits on the open write-gap gate."
        : "Not supervised on this instance — driver loops run only in production."}
    </p>
  );
}

function NotSupervisedNotice() {
  return (
    <p className="text-xs text-text-muted" data-testid="driver-loops-not-supervised">
      Not supervised on this instance — driver loops run only in production. Controls are
      hidden.
    </p>
  );
}

interface FieldProps {
  readonly label: string;
  readonly value: string;
  readonly testId: string;
}

function Field({ label, value, testId }: Readonly<FieldProps>) {
  return (
    <div className="flex items-start gap-1">
      <dt className="text-[10px] font-medium uppercase tracking-wide text-text-muted">
        {label}:
      </dt>
      <dd className="text-text-primary" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}

interface PauseResumeButtonProps {
  readonly loop: DriverLoopStatus;
  readonly tokenPresent: boolean;
  readonly pending: boolean;
  readonly onPause: () => void;
  readonly onResume: () => void;
}

function pauseResumeDescription(tokenPresent: boolean, paused: boolean): string {
  if (!tokenPresent) return "Requires a control token to change driver loops.";
  return paused ? "Resume this loop's schedule." : "Pause this loop's schedule.";
}

function PauseResumeButton({
  loop,
  tokenPresent,
  pending,
  onPause,
  onResume,
}: Readonly<PauseResumeButtonProps>) {
  const disabled = !tokenPresent || pending;
  const handler = loop.paused ? onResume : onPause;
  return (
    <HoverReveal
      id={`driver-loop-pause-resume-description-${loop.name}`}
      description={pauseResumeDescription(tokenPresent, loop.paused)}
    >
      <Button
        variant="ghost"
        className={SUBDUED_WEIGHT_CLASS}
        aria-disabled={disabled}
        aria-busy={pending}
        data-testid={`driver-loop-pause-resume-${loop.name}`}
        onClick={disabled ? undefined : handler}
      >
        {loop.paused ? "Resume" : "Pause"}
      </Button>
    </HoverReveal>
  );
}

interface RunNowButtonProps {
  readonly loop: DriverLoopStatus;
  readonly tokenPresent: boolean;
  readonly pending: boolean;
  readonly onRunNow: () => void;
}

function runNowDescription(tokenPresent: boolean, held: boolean): string {
  if (!tokenPresent) return "Requires a control token to change driver loops.";
  return held ? "This loop can't run right now." : "Run this loop now.";
}

function RunNowButton({ loop, tokenPresent, pending, onRunNow }: Readonly<RunNowButtonProps>) {
  const held = loop.decision.action === "hold";
  const disabled = !tokenPresent || pending || held || loop.run_now_pending;
  return (
    <HoverReveal
      id={`driver-loop-run-now-description-${loop.name}`}
      description={runNowDescription(tokenPresent, held)}
    >
      <Button
        variant="ghost"
        className={SUBDUED_WEIGHT_CLASS}
        aria-disabled={disabled}
        aria-busy={pending}
        data-testid={`driver-loop-run-now-${loop.name}`}
        onClick={disabled ? undefined : onRunNow}
      >
        Run now
      </Button>
    </HoverReveal>
  );
}

interface LoopControlsProps {
  readonly loop: DriverLoopStatus;
  readonly tokenPresent: boolean;
  readonly pending: boolean;
  readonly onPause: () => void;
  readonly onResume: () => void;
  readonly onRunNow: () => void;
}

function LoopControls({
  loop,
  tokenPresent,
  pending,
  onPause,
  onResume,
  onRunNow,
}: Readonly<LoopControlsProps>) {
  return (
    <span className="flex items-center gap-2">
      <PauseResumeButton
        loop={loop}
        tokenPresent={tokenPresent}
        pending={pending}
        onPause={onPause}
        onResume={onResume}
      />
      {loop.enabled && (
        <RunNowButton loop={loop} tokenPresent={tokenPresent} pending={pending} onRunNow={onRunNow} />
      )}
    </span>
  );
}

interface LoopRowProps {
  readonly loop: DriverLoopStatus;
  readonly supervised: boolean;
  readonly tokenPresent: boolean;
  readonly pending: boolean;
  readonly onPause: (name: string) => void;
  readonly onResume: (name: string) => void;
  readonly onRunNow: (name: string) => void;
}

function LoopRow({
  loop,
  supervised,
  tokenPresent,
  pending,
  onPause,
  onResume,
  onRunNow,
}: Readonly<LoopRowProps>) {
  return (
    <li data-testid={`driver-loop-row-${loop.name}`} className="py-2">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-text-primary">{loop.name}</span>
          <AggregateChip
            state={loopStateTone(loop.decision)}
            label={loopStateText(loop.decision)}
            testIdSuffix={`driver-loop-state-${loop.name}`}
          />
        </div>
        <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
          <Field
            label="Next wake"
            value={formatNextWake(loop.decision.next_wake_at)}
            testId={`driver-loop-next-wake-${loop.name}`}
          />
          <Field
            label="Last outcome"
            value={outcomeLabel(loop.last_outcome)}
            testId={`driver-loop-last-outcome-${loop.name}`}
          />
          <Field
            label="Final message"
            value={loop.final_message || "—"}
            testId={`driver-loop-final-message-${loop.name}`}
          />
          <Field
            label="Denials"
            value={String(loop.denial_count)}
            testId={`driver-loop-denials-${loop.name}`}
          />
        </dl>
        {supervised && (
          <div className="mt-2">
            <LoopControls
              loop={loop}
              tokenPresent={tokenPresent}
              pending={pending}
              onPause={() => {
                onPause(loop.name);
              }}
              onResume={() => {
                onResume(loop.name);
              }}
              onRunNow={() => {
                onRunNow(loop.name);
              }}
            />
          </div>
        )}
      </div>
    </li>
  );
}

interface AnnouncementState {
  readonly text: string;
  readonly tone: "ok" | "error";
}

function StatusRegion({ announcement }: Readonly<{ announcement: AnnouncementState | null }>) {
  const toneClass = announcement?.tone === "error" ? "text-btn-destroy" : "text-text-muted";
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-testid="driver-loops-status-region"
      className={announcement === null ? "sr-only" : `text-xs ${toneClass}`}
    >
      {announcement?.text ?? ""}
    </div>
  );
}

interface DriverLoopsBodyProps {
  readonly loops: DriverLoopStatus[];
  readonly supervised: boolean;
  readonly tokenPresent: boolean;
  readonly loaded: boolean;
  readonly error: boolean;
  readonly pendingNames: ReadonlySet<string>;
  readonly onPause: (name: string) => void;
  readonly onResume: (name: string) => void;
  readonly onRunNow: (name: string) => void;
}

function LoopList({
  loops,
  supervised,
  tokenPresent,
  pendingNames,
  onPause,
  onResume,
  onRunNow,
}: Readonly<Omit<DriverLoopsBodyProps, "loaded" | "error">>) {
  return (
    <>
      {!supervised && <NotSupervisedNotice />}
      <ul className="divide-y divide-border/60">
        {loops.map((loop) => (
          <LoopRow
            key={loop.name}
            loop={loop}
            supervised={supervised}
            tokenPresent={tokenPresent}
            pending={pendingNames.has(loop.name)}
            onPause={onPause}
            onResume={onResume}
            onRunNow={onRunNow}
          />
        ))}
      </ul>
    </>
  );
}

function DriverLoopsBody({ loaded, error, loops, ...rest }: Readonly<DriverLoopsBodyProps>) {
  if (!loaded) return <LoadingState />;
  if (error) return <ErrorState />;
  if (loops.length === 0) return <EmptyState supervised={rest.supervised} />;
  return <LoopList loops={loops} {...rest} />;
}

interface DriverLoopMutations {
  readonly pendingNames: ReadonlySet<string>;
  readonly announcement: AnnouncementState | null;
  readonly handlePause: (name: string) => void;
  readonly handleResume: (name: string) => void;
  readonly handleRunNow: (name: string) => void;
}

function useDriverLoopMutations(): DriverLoopMutations {
  const [pendingNames, setPendingNames] = useState<ReadonlySet<string>>(new Set());
  const [announcement, setAnnouncement] = useState<AnnouncementState | null>(null);

  function clearPending(name: string): void {
    setPendingNames((prev) => {
      const next = new Set(prev);
      next.delete(name);
      return next;
    });
  }

  function runMutation(
    name: string,
    action: () => Promise<unknown>,
    successText: string,
    failPrefix: string,
  ): void {
    setPendingNames((prev) => new Set(prev).add(name));
    action().then(
      () => {
        setAnnouncement({ text: successText, tone: "ok" });
        clearPending(name);
      },
      (err: unknown) => {
        setAnnouncement({ text: `${failPrefix}: ${errorMessageOf(err)}`, tone: "error" });
        clearPending(name);
      },
    );
  }

  return {
    pendingNames,
    announcement,
    handlePause: (name) => {
      runMutation(name, () => api.pauseDriverLoop(name), `${name} paused.`, `${name} failed to pause`);
    },
    handleResume: (name) => {
      runMutation(
        name,
        () => api.resumeDriverLoop(name),
        `${name} resumed.`,
        `${name} failed to resume`,
      );
    },
    handleRunNow: (name) => {
      runMutation(
        name,
        () => api.runDriverLoopNow(name),
        `${name} run requested.`,
        `${name} failed to run now`,
      );
    },
  };
}

export default function DriverLoopsPanel() {
  const { loops, supervised, loaded, error } = useDriverLoops();
  const tokenPresent = useControlTokenPresent();
  const { pendingNames, announcement, handlePause, handleResume, handleRunNow } =
    useDriverLoopMutations();

  return (
    <div
      data-testid="driver-loops-panel"
      className="mt-4 space-y-2 rounded-md border border-border/60 bg-surface/40 p-2"
    >
      <SectionHeading level="h2">Driver loops</SectionHeading>
      <StatusRegion announcement={announcement} />
      <DriverLoopsBody
        loops={loops}
        supervised={supervised}
        tokenPresent={tokenPresent}
        loaded={loaded}
        error={error}
        pendingNames={pendingNames}
        onPause={handlePause}
        onResume={handleResume}
        onRunNow={handleRunNow}
      />
    </div>
  );
}
