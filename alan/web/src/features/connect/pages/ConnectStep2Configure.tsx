import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  CONFIG_PROPOSAL_BASE_PRESETS,
  api,
  type ConfigProposalBasePreset,
  type ConfigProposalResult,
  type EngineCodeQualityConfig,
  type EngineConfig,
  type EnginePreflightFinding,
  type ProposableEngineConfigField,
} from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import Button from "../../../components/Button";
import Input from "../../../ui/Input";
import SectionHeading from "../../../ui/SectionHeading";
import { useAction } from "../../../ui/useAction";
import { PREFLIGHT_STYLE } from "../../../components/EngineOpsPanel";
import EnginePresetSeedButton, { type EnginePresetOverlay } from "../components/EnginePresetSeedButton";
import EngineConfigProposalDiffTable from "../components/EngineConfigProposalDiffTable";
import MutationErrorNotice from "../../../components/wizard/MutationErrorNotice";
import WizardFooter from "../components/WizardFooter";
import { type MutationBlock, classifyMutationError } from "../../../lib/connectFlowErrors";
import { type DraftValidationState, settleDraftValidation } from "./draftValidation";
import {
  type Step2Draft,
  buildStep2Payload,
  fallbackFindingOf,
  invalidateStep2Cache,
  parseStep2PoolSizeInput,
  readStep2Cache,
  seedDraft,
  seedDraftFromLive,
  writeStep2Cache,
} from "./connectStep2ConfigureLogic";

export interface ConnectStep2ConfigureProps {
  owner: string;
  repo: string;
}

// A typed, named phase — never a bare spinner. Re-entry re-runs a sonnet
// inference over a fresh checkout (tens of seconds), so "proposing" is
// rendered as an honest, explained wait rather than a frozen table.
type Step2Phase =
  | { kind: "proposing" }
  | { kind: "ready"; proposal: ConfigProposalResult }
  | { kind: "fallback"; finding: EnginePreflightFinding }
  | { kind: "error"; message: string };

const PHASE_STATUS_TEXT: Record<Step2Phase["kind"], string> = {
  proposing: "Reading the repo and drafting a config proposal — this can take up to a minute.",
  ready: "Proposal ready — review and edit below.",
  fallback: "Automatic proposal unavailable — seed from a preset or edit fields manually below.",
  error: "This step couldn't load.",
};

const VALIDATION_DEBOUNCE_MS = 400;

function ValidationChip({ validation }: Readonly<{ validation: DraftValidationState }>) {
  if (validation.status === "loading") {
    return (
      <p className="text-xs text-text-muted" data-testid="step2-validation-loading">
        Validating draft…
      </p>
    );
  }
  if (validation.status === "error") {
    return (
      <p role="alert" className="text-sm text-btn-destroy" data-testid="step2-validation-error">
        {validation.message}
      </p>
    );
  }
  const { finding } = validation;
  return (
    <div className="space-y-1" data-testid="step2-validation">
      <span className={`inline-block rounded px-2 py-0.5 text-xs ${PREFLIGHT_STYLE[finding.status]}`}>
        {finding.status}
      </span>
      <p className="text-xs text-text-muted" data-testid="step2-validation-caption">
        validated as a draft — nothing saved yet
      </p>
      {finding.status !== "pass" && (
        <p role="alert" className="text-sm text-btn-destroy" data-testid="step2-validation-message">
          {finding.message}
        </p>
      )}
    </div>
  );
}

interface EvidenceGroupProps {
  proposal: ConfigProposalResult;
  disabled: boolean;
  onChoosePreset: (preset: ConfigProposalBasePreset) => void;
}

// "What alan read in the repo" sits above the fields it wants to write (rule
// 4). Choosing a different preset re-runs the proposal — the deterministic
// pre-pass recommendation stays informational, never sticky.
function EvidenceGroup({ proposal, disabled, onChoosePreset }: Readonly<EvidenceGroupProps>) {
  return (
    <div className="space-y-2 rounded-md border border-border p-3" data-testid="step2-evidence">
      <SectionHeading level="h3">What alan read in the repo</SectionHeading>
      <p className="text-sm text-text-primary" data-testid="step2-evidence-stacks">
        Detected: {proposal.detected_stacks.join(", ") || "—"}
        {proposal.additional_stacks.length > 0 &&
          ` · Additional stacks: ${proposal.additional_stacks.join(", ")}`}
      </p>
      <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
        <legend className="sr-only">Base preset</legend>
        {CONFIG_PROPOSAL_BASE_PRESETS.map((preset) => (
          <Button
            key={preset}
            variant={preset === proposal.base_preset ? "action" : "ghost"}
            data-testid={`step2-preset-${preset}`}
            aria-pressed={preset === proposal.base_preset}
            onClick={() => {
              // The currently-selected preset can't change the proposal —
              // re-running it would just re-fetch the same result at the
              // cost of a fresh sonnet inference.
              if (preset !== proposal.base_preset) onChoosePreset(preset);
            }}
            disabled={disabled}
          >
            {preset}
          </Button>
        ))}
      </fieldset>
    </div>
  );
}

const EMPTY_CODE_QUALITY: EngineCodeQualityConfig = {
  project_key: "",
  organization: "",
  host_url: "",
  token_env_var: "",
};

const CODE_QUALITY_FIELDS: readonly { key: keyof EngineCodeQualityConfig; label: string }[] = [
  { key: "project_key", label: "Project key" },
  { key: "organization", label: "Organization" },
  { key: "host_url", label: "Host URL" },
  { key: "token_env_var", label: "Token env var" },
];

interface BaselineFieldsBlockProps {
  codeQuality: EngineCodeQualityConfig | null | undefined;
  poolSize: number | null | undefined;
  disabled: boolean;
  onCodeQualityChange: (value: EngineCodeQualityConfig) => void;
  onPoolSizeChange: (value: number | null) => void;
}

// code_quality and pool_size are deliberately excluded from the proposal
// (they're not in _PROPOSABLE_FIELDS) — this block renders them as ordinary
// editable baseline fields, distinct from the diff table by structure
// (a bordered, separately-headed fieldset) and label, not colour alone.
function BaselineFieldsBlock({
  codeQuality,
  poolSize,
  disabled,
  onCodeQualityChange,
  onPoolSizeChange,
}: Readonly<BaselineFieldsBlockProps>) {
  const current = codeQuality ?? EMPTY_CODE_QUALITY;
  return (
    <fieldset
      className="space-y-3 rounded-md border border-dashed border-border p-3"
      data-testid="step2-baseline-fields"
    >
      <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
        Not proposed — edit directly
      </legend>
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="step2-pool-size">
          Worker pool size
        </label>
        <Input
          as="input"
          id="step2-pool-size"
          data-testid="step2-pool-size"
          type="text"
          inputMode="numeric"
          value={poolSize == null ? "" : String(poolSize)}
          onChange={(e) => {
            const parsed = parseStep2PoolSizeInput(e.target.value);
            if (parsed !== "invalid") onPoolSizeChange(parsed);
          }}
          disabled={disabled}
          placeholder="Default"
          tapTarget
          placeholderStyle
        />
      </div>
      <div className="space-y-2">
        <SectionHeading level="h4">Code quality</SectionHeading>
        {CODE_QUALITY_FIELDS.map(({ key, label }) => {
          const fieldId = `step2-cq-${key.replaceAll("_", "-")}`;
          return (
            <div key={key} className="space-y-1">
              <label className="text-xs font-medium text-text-muted" htmlFor={fieldId}>
                {label}
              </label>
              <Input
                as="input"
                id={fieldId}
                data-testid={fieldId}
                type="text"
                value={current[key]}
                onChange={(e) => {
                  onCodeQualityChange({ ...current, [key]: e.target.value });
                }}
                disabled={disabled}
                tapTarget
              />
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}

interface Step2FormProps {
  phase: Step2Phase;
  draft: Step2Draft;
  disabled: boolean;
  validation: DraftValidationState;
  saveBlock: MutationBlock | null;
  onFieldChange: (field: ProposableEngineConfigField, value: unknown) => void;
  onCodeQualityChange: (value: EngineCodeQualityConfig) => void;
  onPoolSizeChange: (value: number | null) => void;
  onChoosePreset: (preset: ConfigProposalBasePreset) => void;
  onSave: () => void;
}

// Renders once a draft exists — in both the "ready" (a table above the
// baseline fields) and "fallback" (baseline fields only) phases, so save
// stays reachable regardless of whether the automatic proposal succeeded.
function Step2Form({
  phase,
  draft,
  disabled,
  validation,
  saveBlock,
  onFieldChange,
  onCodeQualityChange,
  onPoolSizeChange,
  onChoosePreset,
  onSave,
}: Readonly<Step2FormProps>) {
  return (
    <>
      {phase.kind === "ready" && (
        <>
          <EvidenceGroup proposal={phase.proposal} disabled={disabled} onChoosePreset={onChoosePreset} />
          <EngineConfigProposalDiffTable
            diff={phase.proposal.diff}
            values={draft}
            disabled={disabled}
            onFieldChange={onFieldChange}
          />
        </>
      )}
      <BaselineFieldsBlock
        codeQuality={draft.code_quality}
        poolSize={draft.pool_size}
        disabled={disabled}
        onCodeQualityChange={onCodeQualityChange}
        onPoolSizeChange={onPoolSizeChange}
      />
      <ValidationChip validation={validation} />
      {saveBlock !== null && <MutationErrorNotice block={saveBlock} testId="step2-save-error" />}
      <WizardFooter
        primaryLabel="Save configuration"
        onPrimary={onSave}
        primaryDisabled={disabled}
        caption="re-validates on save · nothing writes to the repo yet"
        testId="step2-footer"
      />
    </>
  );
}

interface MountOutcome {
  proposeResult: PromiseSettledResult<ConfigProposalResult>;
  liveResult: PromiseSettledResult<EngineConfig>;
  cachedDraft: Step2Draft | undefined;
}

function resolveMount({ proposeResult, liveResult, cachedDraft }: Readonly<MountOutcome>): {
  live: EngineConfig | null;
  draft: Step2Draft | null;
  phase: Step2Phase;
} {
  if (liveResult.status === "rejected") {
    return { live: null, draft: null, phase: { kind: "error", message: errorMessageOf(liveResult.reason) } };
  }
  const live = liveResult.value;
  if (proposeResult.status === "fulfilled") {
    const proposal = proposeResult.value;
    const draft = cachedDraft ?? seedDraft(proposal, live);
    return { live, draft, phase: { kind: "ready", proposal } };
  }
  const finding = fallbackFindingOf(proposeResult.reason);
  if (finding !== null) {
    return { live, draft: seedDraftFromLive(live), phase: { kind: "fallback", finding } };
  }
  return { live, draft: null, phase: { kind: "error", message: errorMessageOf(proposeResult.reason) } };
}

// Every draft-editing control (the diff table, the baseline fields, the
// preset-seed overlay) only ever renders once `draft !== null`, and `live`
// becomes non-null in the exact same render that sets `draft` (see the mount
// effect below), so `prev` is never actually null when this runs — kept as
// defense-in-depth against a future change desyncing the two, centralized
// here so only one branch needs the coverage exemption instead of four.
function updateDraft(prev: Step2Draft | null, patch: Partial<Step2Draft>): Step2Draft | null {
  /* v8 ignore next */
  if (prev === null) return prev;
  return { ...prev, ...patch };
}

// Reads the engine's own live posture and carries it through the PATCH
// unchanged (buildStep2Payload never lets draft touch posture) — the
// client's obligation, since the server offers no posture guard on this
// PATCH. Re-entry re-runs the proposal unless a warm in-session cache
// already holds one for this engineKey (invalidated by the preset chip).
export default function ConnectStep2Configure({ owner, repo }: Readonly<ConnectStep2ConfigureProps>) {
  const engineKey = `${owner}/${repo}`;
  const navigate = useNavigate();

  // Guards against both a StrictMode double-invoke (the ref already points
  // at engineKey by the time the second invocation runs) and a stale async
  // result landing after engineKey has moved on.
  const activeKey = useRef<string | null>(null);
  const [live, setLive] = useState<EngineConfig | null>(null);
  const [draft, setDraft] = useState<Step2Draft | null>(null);
  const [phase, setPhase] = useState<Step2Phase>({ kind: "proposing" });
  const [validation, setValidation] = useState<DraftValidationState>({ status: "loading" });
  const { busy: saving, run } = useAction("rejection");
  const [saveBlock, setSaveBlock] = useState<MutationBlock | null>(null);

  useEffect(() => {
    if (activeKey.current === engineKey) return;
    activeKey.current = engineKey;
    setPhase({ kind: "proposing" });
    setLive(null);
    setDraft(null);
    setSaveBlock(null);
    const cached = readStep2Cache(engineKey);
    const proposePromise: Promise<ConfigProposalResult> =
      cached !== undefined ? Promise.resolve(cached.proposal) : api.proposeEngineConfig(engineKey, null);
    void Promise.allSettled([proposePromise, api.getEngineConfig(engineKey)]).then(
      ([proposeResult, liveResult]) => {
        if (activeKey.current !== engineKey) return;
        const resolved = resolveMount({ proposeResult, liveResult, cachedDraft: cached?.draft });
        setLive(resolved.live);
        setDraft(resolved.draft);
        setPhase(resolved.phase);
      },
    );
  }, [engineKey]);

  // The single source of truth for the in-session draft cache: every draft
  // mutation (diff-table edit, baseline field, preset overlay, or a fresh
  // repropose) flows through setDraft/setPhase, so re-syncing here on every
  // change — rather than at each individual call site — is what makes
  // "leaving here keeps the draft" (constraint 7) true for edits made after
  // the initial mount, not just for the mount snapshot.
  useEffect(() => {
    if (phase.kind !== "ready" || draft === null) return;
    writeStep2Cache(engineKey, { proposal: phase.proposal, draft });
  }, [engineKey, phase, draft]);

  useEffect(() => {
    if (live === null || draft === null) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setValidation({ status: "loading" });
    const timer = setTimeout(() => {
      void settleDraftValidation(
        api.validateEngineConfigDraft(engineKey, buildStep2Payload(live, draft)),
        () => cancelled,
        setValidation,
      );
    }, VALIDATION_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [engineKey, live, draft]);

  const updateField = (field: ProposableEngineConfigField, value: unknown): void => {
    setDraft((prev) => updateDraft(prev, { [field]: value }));
  };

  const runRepropose = (basePreset: ConfigProposalBasePreset): void => {
    // The preset chips only render once phase.kind === "ready", which is
    // only ever set alongside a non-null live in the same render — see
    // resolveMount above and updateDraft's own comment.
    /* v8 ignore next */
    if (live === null) return;
    invalidateStep2Cache(engineKey);
    setPhase({ kind: "proposing" });
    api.proposeEngineConfig(engineKey, basePreset).then(
      (proposal) => {
        if (activeKey.current !== engineKey) return;
        const nextDraft = seedDraft(proposal, live);
        setDraft(nextDraft);
        setPhase({ kind: "ready", proposal });
      },
      (err: unknown) => {
        if (activeKey.current !== engineKey) return;
        const finding = fallbackFindingOf(err);
        if (finding !== null) {
          setDraft(seedDraftFromLive(live));
          setPhase({ kind: "fallback", finding });
          return;
        }
        setPhase({ kind: "error", message: errorMessageOf(err) });
      },
    );
  };

  const applyPresetSeed = (overlay: EnginePresetOverlay): void => {
    setDraft((prev) => updateDraft(prev, overlay));
  };

  const handleSave = (): void => {
    // The Save button only renders inside Step2Form, which only mounts once
    // draft !== null — see updateDraft's comment for why live is then
    // guaranteed non-null too.
    /* v8 ignore next */
    if (live === null || draft === null) return;
    setSaveBlock(null);
    run(() => api.updateEngineConfig(engineKey, buildStep2Payload(live, draft))).then(
      () => {
        invalidateStep2Cache(engineKey);
        void navigate(`/connect/${owner}/${repo}/dispatchable`);
      },
      (err: unknown) => {
        setSaveBlock(classifyMutationError(err, "save"));
      },
    );
  };

  const disabled = saving;

  return (
    <div className="space-y-4" data-testid="connect-step2-configure">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-text-primary">Configure engine</h2>
        {draft !== null && (
          <div className="flex items-center gap-2" data-testid="step2-preset-shortcut">
            <span className="text-xs text-text-muted">Start from a preset instead:</span>
            <EnginePresetSeedButton onSeed={applyPresetSeed} disabled={disabled} />
          </div>
        )}
      </div>

      <p role="status" aria-live="polite" className="text-sm text-text-muted" data-testid="step2-phase">
        {PHASE_STATUS_TEXT[phase.kind]}
      </p>

      {phase.kind === "error" && (
        <p role="alert" className="text-sm text-btn-destroy" data-testid="step2-error">
          {phase.message}
        </p>
      )}

      {phase.kind === "fallback" && (
        <div className="space-y-1 rounded-md bg-banner-error-tint/15 px-3 py-2" data-testid="step2-fallback">
          <p className="text-sm text-banner-error-fg" data-testid="step2-fallback-message">
            {phase.finding.message}
          </p>
          {phase.finding.remedy !== "" && (
            <p className="text-xs text-text-muted" data-testid="step2-fallback-remedy">
              {phase.finding.remedy}
            </p>
          )}
        </div>
      )}

      {draft !== null && (
        <Step2Form
          phase={phase}
          draft={draft}
          disabled={disabled}
          validation={validation}
          saveBlock={saveBlock}
          onFieldChange={updateField}
          onCodeQualityChange={(value) => {
            setDraft((prev) => updateDraft(prev, { code_quality: value }));
          }}
          onPoolSizeChange={(value) => {
            setDraft((prev) => updateDraft(prev, { pool_size: value }));
          }}
          onChoosePreset={runRepropose}
          onSave={handleSave}
        />
      )}
    </div>
  );
}
