import { render as renderBare, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { EngineSource } from "../../../api";
import { makeEngineConfig } from "../../../test/engineFixtures";
import EngineConfigReadOnlyBase, { type EngineConfigReadOnlyProps } from "./EngineConfigReadOnly";

function render(ui: ReactElement) {
  return renderBare(ui, { wrapper: MemoryRouter });
}

function EngineConfigReadOnly(props: Readonly<Omit<EngineConfigReadOnlyProps, "engineKey">>) {
  return <EngineConfigReadOnlyBase engineKey="sprocket-co/sprocket" {...props} />;
}

const CONFIG = makeEngineConfig({
  repo: { owner: "sprocket-co", repo: "sprocket" },
  code_quality: {
    project_key: "sprocket-co_sprocket",
    organization: "sprocket-co",
    host_url: "https://sonarcloud.io",
    token_env_var: "SONAR_TOKEN",
  },
  forge: {
    repo: { owner: "sprocket-co", repo: "sprocket" },
    credential_env_name: "SPROCKET_GH_TOKEN",
    authoritative_checks: ["Widget CI", "Another Check"],
    enforce_admins: false,
  },
  ci: { main_ci_workflow: "tests.yml", deploy_workflow: "deploy.yml" },
  roles: {
    implementer: { model: "sonnet", effort: "xhigh" },
    judge: { model: "opus", effort: null },
  },
  gate_commands: [["uv", "run", "pytest"], ["npm", "test"]],
  warmup_commands: [["uv", "sync"]],
  base_tools: ["Read", "Grep"],
  team_tools: { implementation: ["Edit", "Write"] },
  transition_graph: { research: ["architecture"], architecture: [] },
  flow_policy: {
    verifier_teams: ["verification"],
    merge_teams: ["pr_merge"],
    terminal_teams: ["deploy_verify"],
    internal_only_teams: ["decomposer"],
    restricted_predecessors: {},
    revisit_cap: 3,
  },
  posture: "self_merge",
  pool_size: 3,
  preset: "sprocket-preset",
});

const FILE_BACKED_SOURCE: EngineSource = {
  slug: "sprocket-co/sprocket",
  in_version_control: true,
  config_path: "config/engines.d/sprocket.yaml",
  posture_ceiling: "propose",
  export_command: null,
};

const NOT_FILE_BACKED_SOURCE: EngineSource = {
  slug: "sprocket-co/sprocket",
  in_version_control: false,
  config_path: null,
  posture_ceiling: "autonomous",
  export_command: "uv run python scripts/export_engine_configs.py sprocket-co/sprocket",
};

const DISCLOSURE_TEST_IDS = [
  "engine-config-readonly-pool-preset-disclosure",
  "engine-config-readonly-commands-disclosure",
  "engine-config-readonly-tools-disclosure",
  "engine-config-readonly-roster-disclosure",
  "engine-config-readonly-forge-disclosure",
];

describe("EngineConfigReadOnly — source pointer variants", () => {
  it("names the config file and the edit-and-merge route for a version-controlled engine", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    expect(screen.getByTestId("engine-config-readonly-config-path")).toHaveTextContent(
      "config/engines.d/sprocket.yaml",
    );
    expect(screen.getByTestId("engine-config-readonly-pointer")).toHaveTextContent(
      /edit that file and merge the change through the normal review process/,
    );
    expect(screen.queryByTestId("engine-config-readonly-export-command")).not.toBeInTheDocument();
    expect(screen.queryByTestId("engine-config-readonly-unknown-source")).not.toBeInTheDocument();
  });

  it("surfaces the exporter command instead of a file pointer when not yet in version control", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={NOT_FILE_BACKED_SOURCE} />);
    expect(screen.getByTestId("engine-config-readonly-export-command")).toHaveTextContent(
      "uv run python scripts/export_engine_configs.py sprocket-co/sprocket",
    );
    expect(screen.queryByTestId("engine-config-readonly-config-path")).not.toBeInTheDocument();
  });

  it("falls back to a generic message when a version-controlled engine has no config_path", () => {
    render(
      <EngineConfigReadOnly config={CONFIG} source={{ ...FILE_BACKED_SOURCE, config_path: null }} />,
    );
    expect(screen.getByTestId("engine-config-readonly-pointer")).toHaveTextContent(
      /edit the config file and merge the change through the normal review process/,
    );
    expect(screen.queryByTestId("engine-config-readonly-config-path")).not.toBeInTheDocument();
  });

  it("falls back to a generic message when a not-yet-tracked engine has no export_command", () => {
    render(
      <EngineConfigReadOnly
        config={CONFIG}
        source={{ ...NOT_FILE_BACKED_SOURCE, export_command: null }}
      />,
    );
    expect(screen.getByTestId("engine-config-readonly-pointer")).toHaveTextContent(
      /Export it to generate its config file, then edit that file and merge the change through review/,
    );
    expect(screen.queryByTestId("engine-config-readonly-export-command")).not.toBeInTheDocument();
  });

  it("renders the unknown-source pointer when source is null, never a silently missing pointer", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={null} />);
    expect(screen.getByTestId("engine-config-readonly-unknown-source")).toHaveTextContent(
      /could not be determined/,
    );
    expect(screen.queryByTestId("engine-config-readonly-config-path")).not.toBeInTheDocument();
    expect(screen.queryByTestId("engine-config-readonly-export-command")).not.toBeInTheDocument();
  });
});

describe("EngineConfigReadOnly — hierarchy: the agent catalogue tile leads, plumbing collapses", () => {
  it("renders the agent catalogue tile with no <details> ancestor, right after the source pointer", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    const tile = screen.getByTestId("agent-catalogue-tile");
    expect(tile.closest("details")).toBeNull();
    const pointer = screen.getByTestId("engine-config-readonly-pointer");
    expect(pointer.compareDocumentPosition(tile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("points the tile at this repository's agent catalogue", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    expect(screen.getByTestId("agent-catalogue-tile-link")).toHaveAttribute(
      "href",
      "/repos/sprocket-co/sprocket/agents",
    );
  });

  it("builds the tile's catalogue link from the route's repository, not from the config document", () => {
    render(
      <EngineConfigReadOnlyBase engineKey="route-owner/route-repo" config={CONFIG} source={FILE_BACKED_SOURCE} />,
    );
    expect(screen.getByTestId("agent-catalogue-tile-link")).toHaveAttribute(
      "href",
      "/repos/route-owner/route-repo/agents",
    );
  });

  it("no longer renders the roles list or the prompt-overrides section", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    expect(screen.queryByTestId("engine-config-readonly-roles")).not.toBeInTheDocument();
    expect(screen.queryByTestId("engine-config-prompt-overrides-disclosure")).not.toBeInTheDocument();
    expect(screen.queryByText("Prompt overrides")).not.toBeInTheDocument();
  });

  it("renders every plumbing group as a closed <details> element", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    for (const testId of DISCLOSURE_TEST_IDS) {
      const details = screen.getByTestId(testId);
      expect(details.tagName).toBe("DETAILS");
      expect(details).not.toHaveAttribute("open");
    }
  });
});

describe("EngineConfigReadOnly — field content", () => {
  it("renders pool size and preset, with placeholders when unset", () => {
    render(
      <EngineConfigReadOnly
        config={{ ...CONFIG, pool_size: null, preset: null }}
        source={FILE_BACKED_SOURCE}
      />,
    );
    expect(screen.getByTestId("engine-config-readonly-pool-size")).toHaveTextContent("Default");
    expect(screen.getByTestId("engine-config-readonly-preset")).toHaveTextContent("None");
  });

  it("renders the configured pool size and preset", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    expect(screen.getByTestId("engine-config-readonly-pool-size")).toHaveTextContent("3");
    expect(screen.getByTestId("engine-config-readonly-preset")).toHaveTextContent("sprocket-preset");
  });

  it("renders every gate and warmup command, and a dash when there are none", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    expect(screen.getByTestId("engine-config-readonly-gate-commands")).toHaveTextContent(
      "uv run pytest",
    );
    expect(screen.getByTestId("engine-config-readonly-gate-commands")).toHaveTextContent("npm test");
    expect(screen.getByTestId("engine-config-readonly-warmup-commands")).toHaveTextContent("uv sync");

    render(
      <EngineConfigReadOnly
        config={{ ...CONFIG, gate_commands: [], warmup_commands: [] }}
        source={FILE_BACKED_SOURCE}
      />,
    );
    expect(screen.getAllByTestId("engine-config-readonly-gate-commands")[1]).toHaveTextContent("—");
  });

  it("renders base tools, or a dash when empty", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    expect(screen.getByTestId("engine-config-readonly-base-tools")).toHaveTextContent("Read, Grep");

    render(<EngineConfigReadOnly config={{ ...CONFIG, base_tools: [] }} source={FILE_BACKED_SOURCE} />);
    expect(screen.getAllByTestId("engine-config-readonly-base-tools")[1]).toHaveTextContent("—");
  });

  it("renders per-team tool allowlists and the team roster as dl rows", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    expect(screen.getByTestId("engine-config-readonly-team-tools-implementation")).toHaveTextContent(
      "Edit, Write",
    );
    expect(screen.getByTestId("engine-config-readonly-roster-research")).toHaveTextContent(
      "architecture",
    );
    expect(screen.getByTestId("engine-config-readonly-roster-architecture")).toHaveTextContent("—");
  });

  it("renders the flow policy alongside the team roster, or the no-flow-policy message when null", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    expect(screen.getByTestId("engine-config-flow-policy-verifier-teams")).toHaveTextContent(
      "verification",
    );
    expect(screen.getByTestId("engine-config-flow-policy-revisit-cap")).toHaveTextContent("3");

    render(
      <EngineConfigReadOnly config={{ ...CONFIG, flow_policy: null }} source={FILE_BACKED_SOURCE} />,
    );
    expect(screen.getAllByTestId("engine-config-flow-policy-empty")[0]).toBeInTheDocument();
  });

  it("renders an em dash for every flow-policy team list that is empty", () => {
    render(
      <EngineConfigReadOnly
        config={{
          ...CONFIG,
          flow_policy: {
            verifier_teams: [],
            merge_teams: [],
            terminal_teams: [],
            internal_only_teams: [],
            restricted_predecessors: {},
            revisit_cap: 0,
          },
        }}
        source={FILE_BACKED_SOURCE}
      />,
    );
    expect(screen.getByTestId("engine-config-flow-policy-verifier-teams")).toHaveTextContent("—");
    expect(screen.getByTestId("engine-config-flow-policy-merge-teams")).toHaveTextContent("—");
    expect(screen.getByTestId("engine-config-flow-policy-terminal-teams")).toHaveTextContent("—");
    expect(screen.getByTestId("engine-config-flow-policy-internal-only-teams")).toHaveTextContent(
      "—",
    );
  });

  it("renders forge fields under a Forge heading, with authoritative checks stably sorted", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    const forge = screen.getByTestId("engine-config-readonly-forge");
    expect(forge).toHaveTextContent("Forge");
    expect(forge).toHaveTextContent("sprocket-co/sprocket");
    expect(forge).toHaveTextContent("SPROCKET_GH_TOKEN");
    expect(forge).toHaveTextContent("Another Check, Widget CI");
  });

  it("renders a no-forge message under a Forge heading when forge is null", () => {
    render(<EngineConfigReadOnly config={{ ...CONFIG, forge: null }} source={FILE_BACKED_SOURCE} />);
    const forge = screen.getByTestId("engine-config-readonly-forge");
    expect(forge).toHaveTextContent("Forge");
    expect(forge).toHaveTextContent("No forge configured.");
  });

  it("renders ci fields under a CI heading, falling back to None for an unset deploy workflow", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    const ci = screen.getByTestId("engine-config-readonly-ci");
    expect(ci).toHaveTextContent("CI");
    expect(ci).toHaveTextContent("tests.yml");
    expect(ci).toHaveTextContent("deploy.yml");

    render(<EngineConfigReadOnly config={{ ...CONFIG, ci: null }} source={FILE_BACKED_SOURCE} />);
    expect(screen.getAllByTestId("engine-config-readonly-ci")[1]).toHaveTextContent("None");
  });

  it("renders code-quality fields under a Code quality heading, or a dash when unset", () => {
    render(<EngineConfigReadOnly config={CONFIG} source={FILE_BACKED_SOURCE} />);
    const codeQuality = screen.getByTestId("engine-config-readonly-code-quality");
    expect(codeQuality).toHaveTextContent("Code quality");
    expect(codeQuality).toHaveTextContent("sprocket-co_sprocket");

    render(
      <EngineConfigReadOnly config={{ ...CONFIG, code_quality: null }} source={FILE_BACKED_SOURCE} />,
    );
    expect(screen.getAllByTestId("engine-config-readonly-code-quality")[1]).toHaveTextContent("—");
  });
});
