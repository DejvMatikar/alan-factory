import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { QueryProvider } from "./api/QueryProvider";
import Button, { GearIcon } from "./components/Button";
import { CommandPaletteProvider } from "./components/CommandPaletteProvider";
import CommandPaletteTrigger from "./components/CommandPaletteTrigger";
import ConsultantModal from "./components/ConsultantModal";
import DispatchReadinessBanner from "./components/DispatchReadinessBanner";
import HealthStatusBadge from "./components/HealthStatusBadge";
import HiddenRepoDialog, { HiddenRepoAnnouncementRegion } from "./components/HiddenRepoDialog";
import HoverReveal from "./components/HoverReveal";
import ProjectFeaturesGate from "./components/ProjectFeaturesGate";
import ProjectFeaturesToggle from "./components/ProjectFeaturesToggle";
import QueuePausedBanner from "./components/QueuePausedBanner";
import SettingsModal from "./components/SettingsModal";
import TopBarRepoSwitcher from "./components/TopBarRepoSwitcher";
import { HealthProvider } from "./api/HealthProvider";
import BuildChat from "./features/chat/pages/BuildChat";
import ConnectWizard from "./features/connect/pages/ConnectWizard";
import EngineConfigEditor from "./features/engine/pages/EngineConfigEditor";
import ExploreChat from "./features/chat/pages/ExploreChat";
import HealthView from "./features/health/pages/HealthView";
import Home from "./features/home/pages/Home";
import OperatorNavLink from "./features/operator/components/OperatorNavLink";
import Operator from "./features/operator/pages/Operator";
import Project from "./features/project/pages/Project";
import Repo from "./features/home/pages/Repo";
import RepoAgents from "./features/engine/pages/RepoAgents";
import RepoEngineConfig from "./features/engine/pages/RepoEngineConfig";
import Workspace from "./features/workspace/pages/Workspace";
import WorkspacePrepChat from "./features/chat/pages/WorkspacePrepChat";
import WorkflowView from "./features/workspace/pages/WorkflowView";
import Projects from "./features/project/pages/Projects";
import { ServerEventsProvider } from "./api/ServerEventsProvider";
import LegacyChatRedirect from "./shell/LegacyChatRedirect";
import RepoShell from "./shell/RepoShell";
import ShellShortcuts from "./shell/ShellShortcuts";
import { TOP_BAR_Z_CLASS } from "./shell/stickyOffsets";
import { ShellExploreProvider, useShellExplore } from "./shell/ShellExploreProvider";
import { SettingsUiProvider, useSettingsUi } from "./shell/SettingsUiProvider";
import { useEngines, useEnginesRefresh } from "./hooks/useEngines";

function EnginesRefresh() {
  useEnginesRefresh();
  return null;
}

function Shell({ children }: Readonly<{ children: ReactNode }>) {
  const { isOpen, open, close } = useSettingsUi();
  const { hiddenEngines, hiddenCount } = useEngines();
  const { target, closeExplore } = useShellExplore();
  return (
    <div className="min-h-screen">
      <header className={`bg-surface sm:sticky sm:top-0 ${TOP_BAR_Z_CLASS}`}>
        <div className="mx-auto flex max-w-[96rem] flex-wrap items-center justify-between gap-y-2 px-6 py-4 sm:flex-nowrap sm:h-16">
          <div className="flex min-w-0 flex-auto items-center gap-2">
            <TopBarRepoSwitcher />
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:flex-nowrap sm:shrink-0">
            <CommandPaletteTrigger />
            <ProjectFeaturesToggle />
            <HiddenRepoDialog hiddenEngines={hiddenEngines} hiddenCount={hiddenCount} />
            <HealthStatusBadge />
            <OperatorNavLink />
            <HoverReveal
              id="settings-gear-description"
              description="App-wide preferences: control token, theme, and other global settings."
              align="end"
            >
              <Button
                variant="ghost"
                iconOnly
                aria-label="Global settings"
                data-testid="settings-gear"
                onClick={open}
              >
                <GearIcon />
              </Button>
            </HoverReveal>
          </div>
        </div>
      </header>
      <HiddenRepoAnnouncementRegion />
      <QueuePausedBanner />
      <DispatchReadinessBanner />
      <ShellShortcuts />
      <EnginesRefresh />
      <main className="mx-auto max-w-[96rem] px-6 py-8">{children}</main>
      {isOpen && <SettingsModal onClose={close} />}
      {target !== null && <ConsultantModal repoKey={target.repoKey} onClose={closeExplore} />}
    </div>
  );
}

export default function App() {
  return (
    <QueryProvider>
    <BrowserRouter>
      <ServerEventsProvider>
      <HealthProvider>
          <SettingsUiProvider>
            <ShellExploreProvider>
            <CommandPaletteProvider>
            <Shell>
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/build" element={<LegacyChatRedirect kind="build" />} />
                <Route path="/connect" element={<ConnectWizard />} />
                <Route path="/connect/:owner/:repo" element={<ConnectWizard />} />
                <Route path="/connect/:owner/:repo/:step" element={<ConnectWizard />} />
                <Route path="/explore/:consultant" element={<LegacyChatRedirect kind="explore" />} />
                <Route path="/health" element={<HealthView />} />
                <Route path="/inbox" element={<Navigate replace to="/health" />} />
                <Route path="/operator" element={<Operator />} />
                <Route path="/repos/:owner/:repo" element={<RepoShell />}>
                  <Route index element={<Repo />} />
                  <Route path="build" element={<BuildChat />} />
                  <Route path="explore/:consultant" element={<ExploreChat />} />
                  <Route path="config" element={<RepoEngineConfig />} />
                  <Route path="agents" element={<RepoAgents />} />
                </Route>
                <Route
                  path="/projects"
                  element={
                    <ProjectFeaturesGate>
                      <Projects />
                    </ProjectFeaturesGate>
                  }
                />
                <Route
                  path="/projects/:projectId"
                  element={
                    <ProjectFeaturesGate>
                      <Project />
                    </ProjectFeaturesGate>
                  }
                />
                <Route
                  path="/projects/:projectId/engine"
                  element={
                    <ProjectFeaturesGate>
                      <EngineConfigEditor />
                    </ProjectFeaturesGate>
                  }
                />
                <Route path="/workspaces/:taskId" element={<Workspace />} />
                <Route path="/workspaces/:taskId/prep-chat" element={<WorkspacePrepChat />} />
                <Route
                  path="/workspaces/:taskId/invocations/:invId"
                  element={<WorkflowView />}
                />
              </Routes>
            </Shell>
            </CommandPaletteProvider>
            </ShellExploreProvider>
          </SettingsUiProvider>
      </HealthProvider>
      </ServerEventsProvider>
    </BrowserRouter>
    </QueryProvider>
  );
}
