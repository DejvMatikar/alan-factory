import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import RepoActionRow, { type RepoActionRowProps } from "./RepoActionRow";
import type { MaintenanceOutcome } from "../hooks/useRepoActions";

const REPO_KEY = "acme/widget";

function renderRow(overrides: Partial<RepoActionRowProps> = {}) {
  const props: RepoActionRowProps = {
    repoKey: REPO_KEY,
    maintenanceOutcome: null,
    maintenanceStopping: false,
    onStopMaintenance: vi.fn(),
    ...overrides,
  };
  return {
    props,
    ...render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<RepoActionRow {...props} />} />
        </Routes>
      </MemoryRouter>,
    ),
  };
}

describe("RepoActionRow — composition", () => {
  it("always renders the wrapping row, even with no outcomes yet", () => {
    renderRow();
    expect(screen.getByTestId(`repo-action-row-${REPO_KEY}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`repo-maintenance-notice-${REPO_KEY}`)).not.toBeInTheDocument();
  });
});

describe("RepoActionRow — maintenance notice", () => {
  it("a running outcome shows a status notice with a workspace link and a Stop control", async () => {
    const outcome: MaintenanceOutcome = { kind: "running", taskId: "ws-1" };
    const onStopMaintenance = vi.fn();
    renderRow({ maintenanceOutcome: outcome, onStopMaintenance });
    const notice = screen.getByTestId(`repo-maintenance-notice-${REPO_KEY}`);
    expect(notice.tagName).toBe("OUTPUT");
    expect(screen.getByRole("status")).toBe(notice);
    const link = within(notice).getByRole("link");
    expect(link).toHaveAttribute("href", "/workspaces/ws-1");
    const stop = screen.getByTestId(`repo-maintenance-stop-${REPO_KEY}`);
    const user = userEvent.setup();
    await user.click(stop);
    expect(onStopMaintenance).toHaveBeenCalledTimes(1);
  });

  it("disables the Stop control while a stop request is in flight", () => {
    const outcome: MaintenanceOutcome = { kind: "running", taskId: "ws-1" };
    renderRow({ maintenanceOutcome: outcome, maintenanceStopping: true });
    expect(screen.getByTestId(`repo-maintenance-stop-${REPO_KEY}`)).toBeDisabled();
  });

  it("a running outcome with a stopError surfaces the failure and keeps the Stop control mounted", () => {
    const outcome: MaintenanceOutcome = { kind: "running", taskId: "ws-1", stopError: "stop-boom" };
    renderRow({ maintenanceOutcome: outcome });
    const notice = screen.getByTestId(`repo-maintenance-notice-${REPO_KEY}`);
    expect(notice).toHaveTextContent("stop-boom");
    expect(screen.getByTestId(`repo-maintenance-stop-${REPO_KEY}`)).toBeInTheDocument();
  });

  it("a stopped outcome rewrites the same notice to report the stopped state with no Stop control", () => {
    const outcome: MaintenanceOutcome = { kind: "stopped", taskId: "ws-1" };
    renderRow({ maintenanceOutcome: outcome });
    const notice = screen.getByTestId(`repo-maintenance-notice-${REPO_KEY}`);
    expect(notice).toHaveTextContent("Stopped");
    expect(within(notice).getByRole("link")).toHaveAttribute("href", "/workspaces/ws-1");
    expect(screen.queryByTestId(`repo-maintenance-stop-${REPO_KEY}`)).not.toBeInTheDocument();
  });

  it("moves focus to the notice when the outcome transitions from running to stopped, so Stop's unmount doesn't drop focus to body", () => {
    const runningOutcome: MaintenanceOutcome = { kind: "running", taskId: "ws-1" };
    const { rerender, props } = renderRow({ maintenanceOutcome: runningOutcome });
    screen.getByTestId(`repo-maintenance-stop-${REPO_KEY}`).focus();
    const stoppedOutcome: MaintenanceOutcome = { kind: "stopped", taskId: "ws-1" };
    rerender(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<RepoActionRow {...props} maintenanceOutcome={stoppedOutcome} />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId(`repo-maintenance-notice-${REPO_KEY}`)).toHaveFocus();
  });

  it("an error outcome reports the failure with no workspace link and no Stop control", () => {
    const outcome: MaintenanceOutcome = { kind: "error", detail: "hc-boom" };
    renderRow({ maintenanceOutcome: outcome });
    const notice = screen.getByTestId(`repo-maintenance-notice-${REPO_KEY}`);
    expect(notice).toHaveTextContent("hc-boom");
    expect(screen.getByRole("status")).toBe(notice);
    expect(within(notice).queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByTestId(`repo-maintenance-stop-${REPO_KEY}`)).not.toBeInTheDocument();
  });
});
