export interface AuditScreen {
  readonly name: string;
  readonly route: string;
}

export const SCREENS: readonly AuditScreen[] = [
  { name: "home", route: "/repos/DejvMatikar/alan" },
  { name: "projects", route: "/projects" },
  { name: "build", route: "/build" },
  { name: "connect-wizard-step1", route: "/connect" },
  {
    name: "connect-wizard-step2",
    route: "/connect/e2e-mobile-audit-connect-co/step3-fixture/configure",
  },
  {
    name: "connect-wizard-step3",
    route: "/connect/e2e-mobile-audit-connect-co/step3-fixture/dispatchable",
  },
  {
    name: "connect-wizard-step4",
    route: "/connect/e2e-mobile-audit-connect-co/step3-fixture/posture",
  },
  { name: "health", route: "/health" },
  { name: "explore-architecture", route: "/explore/architecture" },
  { name: "workspace-decision", route: "/workspaces/:taskId" },
  { name: "prep-chat", route: "/workspaces/:prepChatTaskId/prep-chat" },
  {
    name: "repo-engine-config",
    route: "/repos/e2e-mobile-audit-co/engine-config-fixture/config",
  },
  { name: "agents", route: "/repos/e2e-mobile-audit-co/engine-config-fixture/agents" },
  {
    name: "agents-expanded",
    route:
      "/repos/e2e-mobile-audit-co/engine-config-fixture/agents?agent=team_role%3Aimplementation%2Fimplementer",
  },
  { name: "repo-rail-collapsed", route: "/repos/DejvMatikar/alan?rail=collapsed" },
  { name: "repo-drawer-open", route: "/repos/DejvMatikar/alan?drawer=open" },
  { name: "operator", route: "/operator" },
];
