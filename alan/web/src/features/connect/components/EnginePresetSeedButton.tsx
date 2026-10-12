import { useState } from "react";

import { api, type EngineFlowGraphPolicy, type EnginePresetInfo } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import Button from "../../../components/Button";
import PresetSelectDialog from "../../../components/PresetSelectDialog";

export interface EnginePresetOverlay {
  transition_graph: Record<string, string[]>;
  flow_policy: EngineFlowGraphPolicy | null;
}

export interface EnginePresetSeedButtonProps {
  onSeed: (overlay: EnginePresetOverlay) => void;
  disabled?: boolean;
}

type PresetsState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; presets: EnginePresetInfo[] };

const SEED_DIALOG_DESCRIPTION =
  "Applying a preset overwrites this engine's current team roster, transition graph, " +
  "and flow policy with the preset's. Posture and prompt overrides are never touched, " +
  'and nothing is saved until you click "Save configuration".';

export default function EnginePresetSeedButton({
  onSeed,
  disabled = false,
}: Readonly<EnginePresetSeedButtonProps>) {
  const [presetsState, setPresetsState] = useState<PresetsState>({ status: "idle" });
  const [dialogOpen, setDialogOpen] = useState(false);

  const openDialog = (): void => {
    if (presetsState.status === "ready") {
      setDialogOpen(true);
      return;
    }
    setPresetsState({ status: "loading" });
    api.listEnginePresets().then(
      (presets) => {
        setPresetsState({ status: "ready", presets });
        setDialogOpen(true);
      },
      (err: unknown) => {
        setPresetsState({ status: "error", message: errorMessageOf(err) });
      },
    );
  };

  return (
    <div className="space-y-1" data-testid="engine-preset-seed">
      <Button
        variant="ghost"
        data-testid="engine-preset-seed-open"
        onClick={openDialog}
        disabled={disabled || presetsState.status === "loading"}
      >
        {presetsState.status === "loading" ? "Loading presets…" : "Seed from preset"}
      </Button>
      {presetsState.status === "error" && (
        <p role="alert" data-testid="engine-preset-seed-error" className="text-sm text-btn-destroy">
          {presetsState.message}
        </p>
      )}
      {dialogOpen && presetsState.status === "ready" && (
        <PresetSelectDialog
          testId="engine-preset-seed"
          ariaLabel="Seed roster and transition graph from a preset"
          title="Seed from preset"
          description={SEED_DIALOG_DESCRIPTION}
          options={presetsState.presets.map((preset) => ({
            value: preset.name,
            label: preset.name,
          }))}
          confirmLabel="Overwrite roster, graph & flow policy"
          confirmVariant="destroy"
          onConfirm={(name) => {
            // options above is built 1:1 from presetsState.presets, so name always matches.
            const preset = presetsState.presets.find((candidate) => candidate.name === name)!;
            onSeed({
              transition_graph: preset.config.transition_graph!,
              flow_policy: preset.config.flow_policy ?? null,
            });
            setDialogOpen(false);
          }}
          onClose={() => {
            setDialogOpen(false);
          }}
        />
      )}
    </div>
  );
}
