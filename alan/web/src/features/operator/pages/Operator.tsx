import { lazy, Suspense, useCallback, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { endOperatorSession, type OperatorScope, type OperatorSession } from "../../../api/operator";
import { useInvalidateOperatorStatus, useOperatorStatusQuery } from "../../../api/queries";
import ConfirmDestructiveDialog from "../../../components/ConfirmDestructiveDialog";
import { useMediaQuery, WIDE_SCREEN_QUERY } from "../../../hooks/useMediaQuery";
import ErrorBanner from "../../../ui/ErrorBanner";
import { useAction } from "../../../ui/useAction";
import OperatorBar from "../components/OperatorBar";
import OperatorNotice, { UpdateWarning } from "../components/OperatorNotice";
import ScopePickerDialog, { type ScopePreset } from "../components/ScopePickerDialog";
import { useOperatorSocket, type OperatorPort } from "../hooks/useOperatorSocket";
import { describeSession, failureText } from "../operatorCopy";
import { derivePhase, showsControls, showsTerminal, type OperatorPhase } from "../operatorPhase";
import { OPERATOR_SCOPES } from "../operatorProtocol";

const OperatorTerminal = lazy(() => import("../components/OperatorTerminal"));

const DEFAULT_PRESET: ScopePreset = { scope: "fleet", target: "" };

type PickerMode = "auto" | "open" | "dismissed";

interface EndSession {
  readonly confirming: boolean;
  readonly ended: boolean;
  readonly busy: boolean;
  readonly error: string | null;
  readonly request: () => void;
  readonly cancel: () => void;
  readonly confirm: () => void;
  readonly reset: () => void;
}

interface Picker {
  readonly visible: boolean;
  readonly open: () => void;
  readonly dismiss: () => void;
}

function isOperatorScope(value: string | null): value is OperatorScope {
  return (OPERATOR_SCOPES as readonly (string | null)[]).includes(value);
}

function presetFrom(params: URLSearchParams): ScopePreset | null {
  const scope = params.get("scope");
  return isOperatorScope(scope) ? { scope, target: params.get("target") ?? "" } : null;
}

function sessionIdentity(session: OperatorSession): string {
  return [session.scope, session.target ?? "", session.started_at ?? ""].join("|");
}

function sessionKeyOf(session: OperatorSession | null, wide: boolean): string | null {
  if (!wide || session === null) return null;
  return sessionIdentity(session);
}

function useEndSession(afterEnd: () => void): EndSession {
  const [confirming, setConfirming] = useState(false);
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { busy, run } = useAction();

  const confirm = (): void => {
    setConfirming(false);
    setError(null);
    run(endOperatorSession)
      .then(() => {
        setEnded(true);
        afterEnd();
      })
      .catch((err: unknown) => {
        setError(failureText(err));
      });
  };

  return {
    confirming,
    ended,
    busy,
    error,
    request: () => {
      setConfirming(true);
    },
    cancel: () => {
      setConfirming(false);
    },
    confirm,
    reset: () => {
      setEnded(false);
    },
  };
}

function wantsPicker(phase: OperatorPhase, hasSession: boolean, hasPreset: boolean): boolean {
  return phase.kind === "choose" || (hasPreset && hasSession && showsTerminal(phase));
}

function usePicker(wanted: boolean): Picker {
  const [mode, setMode] = useState<PickerMode>("auto");
  return {
    visible: mode === "open" || (mode === "auto" && wanted),
    open: () => {
      setMode("open");
    },
    dismiss: () => {
      setMode("dismissed");
    },
  };
}

interface SessionDeskProps {
  readonly session: OperatorSession;
  readonly port: OperatorPort;
  readonly end: EndSession;
  readonly withTerminal: boolean;
  readonly onReplace: () => void;
}

function SessionDesk({ session, port, end, withTerminal, onReplace }: Readonly<SessionDeskProps>) {
  const navigate = useNavigate();
  const barRef = useRef<HTMLFieldSetElement>(null);
  const focusBar = useCallback(() => {
    barRef.current?.querySelector("button")?.focus();
  }, []);

  return (
    <div className="space-y-3">
      <OperatorBar
        ref={barRef}
        summary={describeSession(session)}
        endBusy={end.busy}
        onClose={() => {
          void navigate("/");
        }}
        onEnd={end.request}
        onReplace={onReplace}
      />
      {session.update_warning !== null && <UpdateWarning warning={session.update_warning} />}
      {end.error !== null && <ErrorBanner data-testid="operator-end-error">{end.error}</ErrorBanner>}
      {withTerminal && (
        <Suspense
          fallback={
            <output data-testid="operator-terminal-loading" className="block text-sm text-text-muted">
              Loading the terminal…
            </output>
          }
        >
          <OperatorTerminal key={sessionIdentity(session)} port={port} onEscape={focusBar} />
        </Suspense>
      )}
      {end.confirming && (
        <ConfirmDestructiveDialog
          title="End the operator session?"
          body="This stops Claude Code for good and closes the session in every browser. A workspace session's transcript is archived onto that workspace; global and repository sessions archive none."
          confirmLabel="End session"
          testId="operator-end-dialog"
          onConfirm={end.confirm}
          onCancel={end.cancel}
        />
      )}
    </div>
  );
}

export default function Operator() {
  const wide = useMediaQuery(WIDE_SCREEN_QUERY);
  const [params] = useSearchParams();
  const status = useOperatorStatusQuery();
  const invalidateStatus = useInvalidateOperatorStatus();
  const session = status.data?.session ?? null;
  const socket = useOperatorSocket(sessionKeyOf(session, wide), invalidateStatus);
  const end = useEndSession(invalidateStatus);
  const preset = presetFrom(params);
  const phase = derivePhase({
    wide,
    status: status.data,
    statusFailed: status.isError,
    socket: socket.state,
    endedByUser: end.ended,
  });
  const picker = usePicker(wantsPicker(phase, session !== null, preset !== null));

  return (
    <section aria-labelledby="operator-heading" className="space-y-4">
      <h1 id="operator-heading" className="text-xl font-medium text-text-primary">
        Operator
      </h1>
      <OperatorNotice phase={phase} onStart={picker.open} />
      {session !== null && showsControls(phase) && (
        <SessionDesk
          session={session}
          port={socket.port}
          end={end}
          withTerminal={showsTerminal(phase)}
          onReplace={picker.open}
        />
      )}
      {picker.visible && (
        <ScopePickerDialog
          preset={preset ?? DEFAULT_PRESET}
          replacing={session}
          onStarted={() => {
            end.reset();
            invalidateStatus();
            picker.dismiss();
          }}
          onCancel={picker.dismiss}
        />
      )}
    </section>
  );
}
