import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { ConsultantInfo } from "../api";
import { registeredRepoEntries, type RepoRegistryEntry } from "../lib/fold";
import { shortcutHint, type ShortcutId } from "../lib/shortcuts";
import { useEngines } from "../hooks/useEngines";
import { useRepoScope } from "../hooks/useRepoScope";
import { useRepoShortcutActions, type RepoShortcutActions } from "../shell/useRepoShortcutActions";
import ChooserRow from "./ChooserRow";
import type { CommandPaletteStep } from "./commandPaletteContext";
import { useConsultantChoices } from "./ConsultantModal";
import ListChooserModal, { type ListChooserFilter } from "./ListChooserModal";

interface RootCommand {
  key: "build" | "explore" | "build-scoped" | "explore-scoped";
  label: string;
  description: string;
  filterText: string;
  shortcut: ShortcutId;
  run: () => void;
}

interface ScopedTarget {
  key: string;
  name: string;
}

interface RootCommandDeps {
  scoped: ScopedTarget | null;
  actions: RepoShortcutActions;
  onAdvance: (step: CommandPaletteStep) => void;
  onClose: () => void;
}

function baseCommands({ onAdvance }: RootCommandDeps): RootCommand[] {
  return [
    {
      key: "build",
      label: "Build",
      description: "Start a build conversation for a repository.",
      filterText: "Build",
      shortcut: "build-direct",
      run: () => onAdvance({ name: "build-repo" }),
    },
    {
      key: "explore",
      label: "Explore",
      description: "Open a conversation with a consultant.",
      filterText: "Explore",
      shortcut: "explore-direct",
      run: () => onAdvance({ name: "explore-consultant" }),
    },
  ];
}

function scopedCommands({ scoped, actions, onClose }: RootCommandDeps): RootCommand[] {
  if (scoped === null) return [];
  const buildLabel = `Build in ${scoped.name}`;
  const exploreLabel = `Explore ${scoped.name}`;
  return [
    {
      key: "build-scoped",
      label: buildLabel,
      description: "Start a build conversation in the current repository.",
      filterText: `${buildLabel} ${scoped.key}`,
      shortcut: "build-repo",
      run: () => {
        onClose();
        actions.build(scoped.key);
      },
    },
    {
      key: "explore-scoped",
      label: exploreLabel,
      description: "Open a conversation with a consultant about the current repository.",
      filterText: `${exploreLabel} ${scoped.key}`,
      shortcut: "explore-repo",
      run: () => {
        onClose();
        actions.explore(scoped.key);
      },
    },
  ];
}

function stepKey(step: CommandPaletteStep): string {
  return step.name === "explore-repo" ? `explore-repo:${step.consultantKey}` : step.name;
}

function normalize(value: string): string {
  return value.trim().toLowerCase();
}

function filterItems<T>(items: readonly T[], query: string, text: (item: T) => string): T[] {
  const q = normalize(query);
  if (q === "") return [...items];
  return items.filter((item) => normalize(text(item)).includes(q));
}

function useFilter(placeholder: string, ariaLabel: string): { query: string; filter: ListChooserFilter } {
  const [query, setQuery] = useState("");
  return { query, filter: { value: query, onChange: setQuery, placeholder, ariaLabel } };
}

function useRepoList(): readonly RepoRegistryEntry[] {
  const { engines, defaultEngineKey } = useEngines();
  return useMemo(
    () => registeredRepoEntries(engines, defaultEngineKey),
    [engines, defaultEngineKey],
  );
}

interface RootStepProps {
  onAdvance: (step: CommandPaletteStep) => void;
  onClose: () => void;
}

function RootStep({ onAdvance, onClose }: Readonly<RootStepProps>) {
  const { query, filter } = useFilter("Search commands…", "Filter commands");
  const { engines, defaultEngineKey } = useEngines();
  const { key: scopeKey, entry } = useRepoScope(engines, defaultEngineKey);
  const actions = useRepoShortcutActions();
  const scoped = scopeKey === null ? null : { key: scopeKey, name: entry?.title ?? scopeKey };
  const deps = { scoped, actions, onAdvance, onClose };
  const commands = [...baseCommands(deps), ...scopedCommands(deps)];
  const items = filterItems(commands, query, (c) => c.filterText);
  return (
    <ListChooserModal
      ariaLabel="Command palette"
      testId="command-palette"
      title="Command palette"
      description="Choose what to start."
      filter={filter}
      items={items}
      getItemKey={(c) => c.key}
      getItemTestId={(c) => `palette-command-${c.key}`}
      renderItem={(c) => (
        <ChooserRow label={c.label} description={c.description} hint={shortcutHint(c.shortcut)} />
      )}
      onSelect={(c) => c.run()}
      onClose={onClose}
    />
  );
}

interface RepoStepProps {
  ariaLabel: string;
  title: string;
  description: string;
  onSelect: (repo: RepoRegistryEntry) => void;
  onClose: () => void;
}

function RepoStep({ ariaLabel, title, description, onSelect, onClose }: Readonly<RepoStepProps>) {
  const repos = useRepoList();
  const { query, filter } = useFilter("Search repositories…", "Filter repositories");
  const items = filterItems(repos, query, (r) => `${r.title} ${r.key}`);
  return (
    <ListChooserModal
      ariaLabel={ariaLabel}
      testId="command-palette"
      title={title}
      description={description}
      filter={filter}
      items={items}
      getItemKey={(r) => r.key}
      getItemTestId={(r) => `palette-repo-${r.key}`}
      renderItem={(r) => <ChooserRow label={r.title} description={r.key} />}
      onSelect={onSelect}
      onClose={onClose}
    />
  );
}

function BuildRepoStep({ onClose }: Readonly<{ onClose: () => void }>) {
  const navigate = useNavigate();
  return (
    <RepoStep
      ariaLabel="Command palette — choose a repository to build"
      title="Build"
      description="Choosing a repository starts a build conversation for it."
      onClose={onClose}
      onSelect={(repo) => {
        onClose();
        void navigate(`/build?engine=${encodeURIComponent(repo.key)}`);
      }}
    />
  );
}

interface ExploreConsultantStepProps {
  onAdvance: (step: CommandPaletteStep) => void;
  onClose: () => void;
}

function ExploreConsultantStep({ onAdvance, onClose }: Readonly<ExploreConsultantStepProps>) {
  const { query, filter } = useFilter("Search consultants…", "Filter consultants");
  const { items: consultants, notice } = useConsultantChoices();
  const items = filterItems(consultants, query, (c) => c.label);
  return (
    <ListChooserModal
      ariaLabel="Command palette — choose a consultant"
      testId="command-palette"
      title="Explore"
      description="Choosing a consultant opens a conversation with them; nothing else starts automatically."
      filter={filter}
      notice={notice}
      items={items}
      getItemKey={(c) => c.key}
      getItemTestId={(c) => `palette-consultant-${c.key}`}
      renderItem={(c) => <ChooserRow label={c.label} description={c.description} />}
      onSelect={(c: ConsultantInfo) =>
        onAdvance({ name: "explore-repo", consultantKey: c.key, consultantLabel: c.label })
      }
      onClose={onClose}
    />
  );
}

interface ExploreRepoStepProps {
  consultantKey: string;
  consultantLabel: string;
  onClose: () => void;
}

function ExploreRepoStep({ consultantKey, consultantLabel, onClose }: Readonly<ExploreRepoStepProps>) {
  const navigate = useNavigate();
  return (
    <RepoStep
      ariaLabel={`Command palette — choose a repository to explore with ${consultantLabel}`}
      title={`Explore / ${consultantLabel}`}
      description="Choosing a repository starts an explore conversation for it."
      onClose={onClose}
      onSelect={(repo) => {
        onClose();
        void navigate(`/explore/${encodeURIComponent(consultantKey)}?engine=${encodeURIComponent(repo.key)}`);
      }}
    />
  );
}

interface Props {
  step: CommandPaletteStep;
  onAdvance: (step: CommandPaletteStep) => void;
  onClose: () => void;
}

export default function CommandPalette({ step, onAdvance, onClose }: Readonly<Props>) {
  const key = stepKey(step);
  if (step.name === "root") return <RootStep key={key} onAdvance={onAdvance} onClose={onClose} />;
  if (step.name === "build-repo") return <BuildRepoStep key={key} onClose={onClose} />;
  if (step.name === "explore-consultant") {
    return <ExploreConsultantStep key={key} onAdvance={onAdvance} onClose={onClose} />;
  }
  return (
    <ExploreRepoStep
      key={key}
      consultantKey={step.consultantKey}
      consultantLabel={step.consultantLabel}
      onClose={onClose}
    />
  );
}
