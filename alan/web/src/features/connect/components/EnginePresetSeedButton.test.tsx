import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { EngineConfig } from "../../../api";
import { makeEngineConfig } from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import EnginePresetSeedButton from "./EnginePresetSeedButton";

const BASE_CONFIG = makeEngineConfig({
  repo: { owner: "_preset", repo: "python" },
  gate_commands: [["uv", "run", "pytest"]],
  warmup_commands: [["uv", "sync"]],
  transition_graph: { research: ["brainstorming"], brainstorming: ["research"] },
  flow_policy: {
    verifier_teams: ["verification"],
    merge_teams: ["pr_merge"],
    terminal_teams: ["deploy_verify"],
    internal_only_teams: [],
    restricted_predecessors: {},
    revisit_cap: 3,
  },
  preset: "python",
});

const NODE_CONFIG = {
  ...BASE_CONFIG,
  repo: { owner: "_preset", repo: "node" },
  transition_graph: { architecture: [] },
  flow_policy: null,
  preset: "node",
};

function mockPresets(presets: { name: string; config: EngineConfig }[], fetchCount?: { n: number }) {
  server.use(
    http.get("/api/engine-presets", () => {
      if (fetchCount) fetchCount.n += 1;
      return HttpResponse.json({ presets });
    }),
  );
}

describe("EnginePresetSeedButton", () => {
  it("renders the open button idle, with no dialog", () => {
    render(<EnginePresetSeedButton onSeed={vi.fn()} />);
    expect(screen.getByTestId("engine-preset-seed-open")).toHaveTextContent("Seed from preset");
    expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
  });

  it("fetches presets on open and shows a select listing each preset name", async () => {
    mockPresets([
      { name: "python", config: BASE_CONFIG },
      { name: "node", config: NODE_CONFIG },
    ]);
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={vi.fn()} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    expect(await screen.findByTestId("engine-preset-seed-dialog")).toBeInTheDocument();
    const select = screen.getByTestId("engine-preset-seed-select");
    expect(select).toHaveValue("python");
    expect(screen.getByRole("option", { name: "node" })).toBeInTheDocument();
  });

  it("confirming overlays only transition_graph and flow_policy from the selected preset, then closes", async () => {
    mockPresets([
      { name: "python", config: BASE_CONFIG },
      { name: "node", config: NODE_CONFIG },
    ]);
    const onSeed = vi.fn();
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={onSeed} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    await screen.findByTestId("engine-preset-seed-dialog");
    await user.selectOptions(screen.getByTestId("engine-preset-seed-select"), "node");
    await user.click(screen.getByTestId("engine-preset-seed-confirm"));
    expect(onSeed).toHaveBeenCalledWith({
      transition_graph: NODE_CONFIG.transition_graph,
      flow_policy: NODE_CONFIG.flow_policy,
    });
    expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
  });

  it("reopening after a successful fetch does not refetch the preset list", async () => {
    const fetchCount = { n: 0 };
    mockPresets([{ name: "python", config: BASE_CONFIG }], fetchCount);
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={vi.fn()} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    await screen.findByTestId("engine-preset-seed-dialog");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    await screen.findByTestId("engine-preset-seed-dialog");
    expect(fetchCount.n).toBe(1);
  });

  it("Cancel closes the dialog without calling onSeed", async () => {
    mockPresets([{ name: "python", config: BASE_CONFIG }]);
    const onSeed = vi.fn();
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={onSeed} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    await screen.findByTestId("engine-preset-seed-dialog");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
    expect(onSeed).not.toHaveBeenCalled();
  });

  it("Escape closes the dialog", async () => {
    mockPresets([{ name: "python", config: BASE_CONFIG }]);
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={vi.fn()} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    const dialog = await screen.findByTestId("engine-preset-seed-dialog");
    fireEvent.keyDown(dialog.querySelector("div")!, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
    });
  });

  it("Ctrl+Enter confirms the currently selected preset", async () => {
    mockPresets([{ name: "python", config: BASE_CONFIG }]);
    const onSeed = vi.fn();
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={onSeed} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    const dialog = await screen.findByTestId("engine-preset-seed-dialog");
    fireEvent.keyDown(dialog.querySelector("div")!, { key: "Enter", ctrlKey: true });
    expect(onSeed).toHaveBeenCalledWith({
      transition_graph: BASE_CONFIG.transition_graph,
      flow_policy: BASE_CONFIG.flow_policy,
    });
  });

  it("the dialog's native cancel event closes it", async () => {
    mockPresets([{ name: "python", config: BASE_CONFIG }]);
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={vi.fn()} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    const dialog = await screen.findByTestId("engine-preset-seed-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    await waitFor(() => {
      expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
    });
  });

  it("clicking the dialog backdrop closes it", async () => {
    mockPresets([{ name: "python", config: BASE_CONFIG }]);
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={vi.fn()} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    const dialog = await screen.findByTestId("engine-preset-seed-dialog");
    fireEvent.click(dialog);
    await waitFor(() => {
      expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
    });
  });

  it("shows an inline error and keeps the dialog closed when the preset fetch fails", async () => {
    server.use(
      http.get("/api/engine-presets", () =>
        HttpResponse.json({ detail: "preset service unavailable" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={vi.fn()} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    expect(await screen.findByTestId("engine-preset-seed-error")).toHaveTextContent(
      "preset service unavailable",
    );
    expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
  });

  it("disables Confirm and ignores Ctrl+Enter when the preset catalog is empty", async () => {
    mockPresets([]);
    const onSeed = vi.fn();
    const user = userEvent.setup();
    render(<EnginePresetSeedButton onSeed={onSeed} />);
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    const dialog = await screen.findByTestId("engine-preset-seed-dialog");
    expect(screen.getByTestId("engine-preset-seed-confirm")).toBeDisabled();
    fireEvent.keyDown(dialog.querySelector("div")!, { key: "Enter", ctrlKey: true });
    expect(onSeed).not.toHaveBeenCalled();
  });

  it("disables the open button when disabled is true", () => {
    render(<EnginePresetSeedButton onSeed={vi.fn()} disabled />);
    expect(screen.getByTestId("engine-preset-seed-open")).toBeDisabled();
  });
});
