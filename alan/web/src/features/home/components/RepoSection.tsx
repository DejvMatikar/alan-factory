import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { GearIcon } from "../../../components/Button";
import { useHealthContext } from "../../../api/HealthProvider";
import { accentTextClass, accentVars, type ResolvedAccent } from "../../../lib/repoAccent";
import { SCROLL_COLUMN_CLASS, STICKY_REGION_CLASS } from "../../../shell/stickyOffsets";
import { useDefaultEngineState } from "../../../hooks/useEngines";
import { engineKeyMatchesRepo, type RepoHeaderModel, type RepoSectionModel } from "../../../lib/fold";
import { maintenanceGate, posturePathFor, type MaintenanceGate } from "./postureGate";
import RepoActionControl from "./RepoActionControl";
import RepoActionRow from "./RepoActionRow";
import RepoActionToolbar from "./RepoActionToolbar";
import RepoScopedCountLink from "./RepoScopedCountLink";
import RepoStatusStrip from "./RepoStatusStrip";
import RepoWorkspaceTable from "./RepoWorkspaceTable";
import type { Tab } from "./tabs";
import { useRepoActions, type UseRepoActionsResult } from "../hooks/useRepoActions";

export interface RepoSectionProps {
  readonly header: RepoHeaderModel;
  readonly section: RepoSectionModel;
  readonly ready: boolean;
  readonly tab: Tab;
  readonly reload: () => void;
  readonly showProjects: boolean;
  readonly resolvedAccent: ResolvedAccent | null;
  readonly repoChrome?: boolean;
  readonly tabs?: ReactNode;
  readonly postureResolved: boolean;
}

interface RepoSectionChromeProps {
  readonly section: RepoSectionModel;
  readonly showProjects: boolean;
  readonly actions: UseRepoActionsResult;
}

function RepoSectionChrome({ section, showProjects, actions }: Readonly<RepoSectionChromeProps>) {
  return (
    <>
      <RepoActionToolbar section={section} showProjects={showProjects} actions={actions} />
      <RepoStatusStrip engineKey={section.key} />
    </>
  );
}

interface MaintenanceActionProps {
  readonly onClick?: () => void;
  readonly disabled?: boolean;
  readonly ariaBusy?: boolean;
  readonly linkTo?: string;
}

function maintenanceControlProps(
  gate: MaintenanceGate,
  repoKey: string,
  actions: UseRepoActionsResult,
): { description: string } & MaintenanceActionProps {
  switch (gate.kind) {
    case "unresolved":
      return {
        description: "Checking this repository's posture…",
        disabled: true,
        ariaBusy: true,
      };
    case "permitted":
      return {
        description: "Immediately starts maintenance work that costs money.",
        onClick: actions.startMaintenance,
        disabled: actions.maintenanceDispatching,
      };
    case "gated":
      return { description: gate.reason, linkTo: posturePathFor(repoKey) };
  }
}

function hideDescription(isDefault: boolean): string {
  return isDefault
    ? "The default repository can't be hidden."
    : "Parks this repository: attempts to stop its work and hides it everywhere until you restore it.";
}

function settingsLinkTo(repoKey: string): string {
  const slashIndex = repoKey.indexOf("/");
  if (slashIndex === -1) throw new Error(`invalid repoKey: ${repoKey}`);
  return `/repos/${repoKey.slice(0, slashIndex)}/${repoKey.slice(slashIndex + 1)}/config`;
}

interface MaintenanceHeaderActionProps {
  readonly repoKey: string;
  readonly posture: RepoHeaderModel["posture"];
  readonly postureResolved: boolean;
  readonly actions: UseRepoActionsResult;
}

function MaintenanceHeaderAction({
  repoKey,
  posture,
  postureResolved,
  actions,
}: Readonly<MaintenanceHeaderActionProps>) {
  const gate = maintenanceGate(postureResolved, posture);
  const { description, ...controlProps } = maintenanceControlProps(gate, repoKey, actions);
  return (
    <RepoActionControl
      repoKey={repoKey}
      testId="repo-action-maintenance"
      label="Maintenance"
      description={description}
      {...controlProps}
    />
  );
}

function repoHealthAriaLabel(repoKey: string, count: number): string {
  return `Health for ${repoKey} — ${String(count)} finding${count === 1 ? "" : "s"}`;
}

function useRepoHealthFindingCount(repoKey: string, defaultKey: string | null): number {
  const { healthState } = useHealthContext();
  if (healthState === null) return 0;
  return healthState.findings.filter((f) => engineKeyMatchesRepo(f.engine_key, repoKey, defaultKey))
    .length;
}

interface RepoHeaderProps {
  readonly repoKey: string;
  readonly title: string;
  readonly posture: RepoHeaderModel["posture"];
  readonly postureResolved: boolean;
  readonly actions: UseRepoActionsResult;
  readonly resolvedAccent: ResolvedAccent | null;
}

function RepoHeader({
  repoKey,
  title,
  posture,
  postureResolved,
  actions,
  resolvedAccent,
}: Readonly<RepoHeaderProps>) {
  const { key: defaultEngineKey, settled } = useDefaultEngineState();
  const healthCount = useRepoHealthFindingCount(repoKey, defaultEngineKey);
  const isDefault = repoKey === defaultEngineKey;
  return (
    <div className="space-y-2">
      <h1 className={`text-xl font-semibold ${accentTextClass(resolvedAccent)}`}>{title}</h1>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <MaintenanceHeaderAction
          repoKey={repoKey}
          posture={posture}
          postureResolved={postureResolved}
          actions={actions}
        />
        <div className="flex flex-wrap items-start justify-end gap-2">
          <RepoActionControl
            repoKey={repoKey}
            testId="repo-action-hide"
            label={actions.hiding ? "Hiding…" : "Hide"}
            description={hideDescription(isDefault)}
            onClick={actions.hideRepo}
            disabled={isDefault || !settled || actions.hiding}
          />
          <RepoScopedCountLink
            label="Health"
            to={`/health?engine=${encodeURIComponent(repoKey)}`}
            count={healthCount}
            ariaLabel={repoHealthAriaLabel(repoKey, healthCount)}
            testId={`repo-health-${repoKey}`}
            description={`${repoKey}'s health findings.`}
          />
          <RepoActionControl
            repoKey={repoKey}
            testId="repo-action-settings"
            label="Settings"
            description="Read how this engine is configured."
            linkTo={settingsLinkTo(repoKey)}
            ariaLabel={`Settings for ${repoKey}`}
            icon={<GearIcon />}
          />
        </div>
      </div>
    </div>
  );
}

export default function RepoSection({
  header,
  section,
  ready,
  tab,
  reload,
  showProjects,
  resolvedAccent,
  repoChrome,
  tabs,
  postureResolved,
}: Readonly<RepoSectionProps>) {
  const chrome = repoChrome ?? true;
  const actions = useRepoActions(section.key);
  return (
    <section
      data-testid={`repo-section-${section.key}`}
      style={accentVars(resolvedAccent)}
      className={`space-y-2 ${SCROLL_COLUMN_CLASS}`}
    >
      {chrome ? (
        <div className={`${STICKY_REGION_CLASS} space-y-2`}>
          <RepoHeader
            repoKey={section.key}
            title={header.title}
            posture={header.posture}
            postureResolved={postureResolved}
            actions={actions}
            resolvedAccent={resolvedAccent}
          />
          <RepoActionRow
            repoKey={section.key}
            maintenanceOutcome={actions.maintenanceOutcome}
            maintenanceStopping={actions.maintenanceStopping}
            onStopMaintenance={actions.stopMaintenance}
          />
        </div>
      ) : (
        <h1 className="sr-only">{header.title}</h1>
      )}
      {chrome && <RepoSectionChrome section={section} showProjects={showProjects} actions={actions} />}
      {tabs}
      <RepoWorkspaceTable
        section={section}
        tab={tab}
        reload={reload}
        showProjects={showProjects}
        loading={!ready}
      />
      {showProjects && chrome && (
        <div className="text-xs text-text-muted" data-testid={`repo-footer-${section.key}`}>
          <Link to="/projects" className="inline-flex min-h-11 items-center text-btn-action underline">
            {section.shippedCount} shipped · {section.archivedCount} archived →
          </Link>
        </div>
      )}
    </section>
  );
}
