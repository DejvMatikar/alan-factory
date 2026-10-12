import { useCallback, type KeyboardEvent } from "react";

import HoverReveal from "../../../components/HoverReveal";
import type { ResolvedAccent } from "../../../lib/repoAccent";
import ErrorBanner from "../../../ui/ErrorBanner";
import type { RepoHeaderModel, RepoSectionModel } from "../../../lib/fold";
import NeedsYouLines from "./NeedsYouLines";
import RepoSection from "./RepoSection";
import type { Tab } from "./tabs";
import type { ResumeEntry } from "../hooks/useConnectStates";

const TABS: Tab[] = ["active", "archived"];

const TAB_LABELS: Record<Tab, string> = {
  active: "Active",
  archived: "Archived",
};

const TAB_DESCRIPTIONS: Record<Tab, string> = {
  active: "Shows workspaces currently in progress.",
  archived: "Shows workspaces that have finished or been archived.",
};

interface RepoTabsProps {
  readonly tab: Tab;
  readonly onSelect: (t: Tab) => void;
}

function RepoTabs({ tab, onSelect }: Readonly<RepoTabsProps>) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>): void => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const nextTab: Tab = e.key === "ArrowRight" ? "archived" : "active";
      onSelect(nextTab);
      const nextIndex = TABS.indexOf(nextTab);
      const nextTabButton = e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')[nextIndex];
      /* v8 ignore next */
      nextTabButton?.focus();
    },
    [onSelect],
  );
  return (
    <div role="tablist" tabIndex={-1} className="flex gap-2" onKeyDown={handleKeyDown}>
      {TABS.map((t) => {
        const descriptionId = `repo-tab-${t}-description`;
        return (
          <HoverReveal
            key={t}
            id={descriptionId}
            description={TAB_DESCRIPTIONS[t]}
            wrapperRole="presentation"
          >
            <button
              type="button"
              role="tab"
              aria-selected={tab === t}
              aria-describedby={descriptionId}
              tabIndex={tab === t ? 0 : -1}
              onClick={() => {
                onSelect(t);
              }}
              className={
                tab === t
                  ? "min-h-11 rounded-md bg-surface-elevated px-3 py-1.5 text-sm font-medium text-text-primary"
                  : "min-h-11 rounded-md px-3 py-1.5 text-sm font-medium text-text-muted hover:text-text-primary"
              }
            >
              {TAB_LABELS[t]}
            </button>
          </HoverReveal>
        );
      })}
    </div>
  );
}

export interface RepoPageBodyProps {
  readonly tab: Tab;
  readonly onSelectTab: (t: Tab) => void;
  readonly loadError: string | null;
  readonly ready: boolean;
  readonly header: RepoHeaderModel;
  readonly section: RepoSectionModel;
  readonly reload: () => void;
  readonly showProjects: boolean;
  readonly resolvedAccent: ResolvedAccent | null;
  readonly postureResolved: boolean;
  readonly hostSetupMessage: string | null;
  readonly resumes: readonly ResumeEntry[];
}

export default function RepoPageBody({
  tab,
  onSelectTab,
  loadError,
  ready,
  header,
  section,
  reload,
  showProjects,
  resolvedAccent,
  postureResolved,
  hostSetupMessage,
  resumes,
}: Readonly<RepoPageBodyProps>) {
  const tableReady = ready || loadError !== null;
  return (
    <>
      {loadError !== null && (
        <div data-testid="home-load-error-slot">
          <ErrorBanner data-testid="home-load-error">{loadError}</ErrorBanner>
        </div>
      )}

      <div data-testid="home-needs-you-slot">
        <NeedsYouLines hostSetupMessage={hostSetupMessage} resumes={resumes} />
      </div>

      <div data-testid="home-repo-sections">
        <RepoSection
          header={header}
          section={section}
          ready={tableReady}
          tab={tab}
          reload={reload}
          showProjects={showProjects}
          resolvedAccent={resolvedAccent}
          postureResolved={postureResolved}
          tabs={<RepoTabs tab={tab} onSelect={onSelectTab} />}
        />
      </div>
    </>
  );
}
