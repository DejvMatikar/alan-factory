import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import HomeRow, { type HomeRowAction, type HomeRowCells } from "./HomeRow";

const CELLS: HomeRowCells = {
  primary: "Primary",
  team: "Team",
  state: "State",
  phase: "Phase",
  done: "Done",
  active: "Active",
  cost: "Cost",
  updated: "Updated",
};

function action(overrides: Partial<HomeRowAction> = {}): HomeRowAction {
  return {
    testId: "workspace-row-action-w1",
    label: "Stop",
    description: "Stop this workspace's running job.",
    disabled: false,
    busy: false,
    error: null,
    onClick: vi.fn(),
    icon: <span data-testid="test-action-icon" aria-hidden="true" />,
    ariaLabel: "Stop — w1",
    ...overrides,
  };
}

function renderRow(children: React.ReactElement) {
  return render(
    <MemoryRouter>
      <table>
        <tbody>{children}</tbody>
      </table>
    </MemoryRouter>,
  );
}

describe("HomeRow — cells", () => {
  it("renders all eight cells plus a blank action cell when no action is given", () => {
    renderRow(<HomeRow kind="project" testId="row-1" cells={CELLS} />);
    const row = screen.getByTestId("row-1");
    expect(row).toHaveTextContent("Primary");
    expect(row).toHaveTextContent("Active");
    expect(row).toHaveTextContent("Updated");
    expect(row.querySelectorAll("td")).toHaveLength(9);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("applies distinct row classes per kind", () => {
    renderRow(<HomeRow kind="unassigned" testId="row-4" cells={CELLS} />);
    expect(screen.getByTestId("row-4").className).toContain("text-text-muted");
  });

  it("middle-aligns every cell in a feature row but not project or unassigned rows", () => {
    renderRow(<HomeRow kind="feature" testId="row-feature-align" cells={CELLS} />);
    expect(screen.getByTestId("row-feature-align").className).toContain("[&>td]:align-middle");
    expect(screen.getByTestId("row-feature-align").className).not.toContain("[&>td]:align-top");

    renderRow(<HomeRow kind="project" testId="row-project-align" cells={CELLS} />);
    expect(screen.getByTestId("row-project-align").className).not.toContain("[&>td]:align-middle");

    renderRow(<HomeRow kind="unassigned" testId="row-unassigned-align" cells={CELLS} />);
    expect(screen.getByTestId("row-unassigned-align").className).not.toContain("[&>td]:align-middle");
  });

  it("caps the feature row's primary cell width so its content truncates, unlike project/unassigned rows", () => {
    renderRow(<HomeRow kind="feature" testId="row-5" cells={CELLS} />);
    const featureCellClass = screen.getByTestId("row-5").querySelectorAll("td")[1]?.className ?? "";
    expect(featureCellClass).toContain("w-full");
    expect(featureCellClass).toContain("max-w-0");

    renderRow(<HomeRow kind="project" testId="row-6" cells={CELLS} />);
    const projectCellClass = screen.getByTestId("row-6").querySelectorAll("td")[1]?.className ?? "";
    expect(projectCellClass).not.toContain("max-w-0");

    renderRow(<HomeRow kind="unassigned" testId="row-7" cells={CELLS} />);
    const unassignedCellClass = screen.getByTestId("row-7").querySelectorAll("td")[1]?.className ?? "";
    expect(unassignedCellClass).not.toContain("max-w-0");
  });

  it("renders the action cell first, ahead of the primary cell", () => {
    renderRow(<HomeRow kind="feature" testId="row-action-first" cells={CELLS} action={action()} />);
    const cells = screen.getByTestId("row-action-first").querySelectorAll("td");
    expect(within(cells[0] as HTMLElement).getByTestId("workspace-row-action-w1")).toBeInTheDocument();
    expect(cells[1]?.textContent).toBe("Primary");
  });

  it("renders a blank first cell ahead of the primary cell when no action is given", () => {
    renderRow(<HomeRow kind="project" testId="row-blank-first" cells={CELLS} />);
    const cells = screen.getByTestId("row-blank-first").querySelectorAll("td");
    expect(cells[0]?.textContent).toBe("");
    expect(cells[1]?.textContent).toBe("Primary");
  });
});

describe("HomeRow — action control", () => {
  it("renders exactly one control when an action is given", async () => {
    const onClick = vi.fn();
    renderRow(<HomeRow kind="feature" testId="row-action" cells={CELLS} action={action({ onClick })} />);
    const button = screen.getByTestId("workspace-row-action-w1");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    const user = userEvent.setup();
    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("never renders a checkbox role", () => {
    renderRow(<HomeRow kind="feature" testId="row-nocheckbox" cells={CELLS} action={action()} />);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("carries a busy accessible name and aria-busy while an action is in flight", () => {
    renderRow(
      <HomeRow
        kind="feature"
        testId="row-busy"
        cells={CELLS}
        action={action({ label: "Stopping…", ariaLabel: "Stopping… — w1", busy: true, disabled: true })}
      />,
    );
    const button = screen.getByTestId("workspace-row-action-w1");
    expect(button).toHaveAccessibleName("Stopping… — w1");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveAttribute("aria-disabled", "true");
  });

  it("renders an inline alert when the action carries an error", () => {
    renderRow(<HomeRow kind="feature" testId="row-error" cells={CELLS} action={action({ error: "boom" })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });

  it("renders no alert when the action carries no error", () => {
    renderRow(<HomeRow kind="feature" testId="row-noerror" cells={CELLS} action={action()} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders the action's icon rather than its visible label text", () => {
    renderRow(<HomeRow kind="feature" testId="row-icon" cells={CELLS} action={action()} />);
    expect(screen.getByTestId("test-action-icon")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-row-action-w1")).not.toHaveTextContent("Stop");
  });

  it("keeps the action cell at a constant w-11 width whether the action is resting or errored", () => {
    renderRow(<HomeRow kind="feature" testId="row-width-resting" cells={CELLS} action={action()} />);
    const restingCell = screen.getByTestId("row-width-resting").querySelectorAll("td")[0];
    expect(restingCell?.className).toContain("w-11");

    renderRow(
      <HomeRow
        kind="feature"
        testId="row-width-error"
        cells={CELLS}
        action={action({ error: "a fairly long error message that could otherwise widen the column" })}
      />,
    );
    const errorCell = screen.getByTestId("row-width-error").querySelectorAll("td")[0];
    expect(errorCell?.className).toContain("w-11");
  });

  it("wraps a long error message inside the fixed-width action cell instead of widening it", () => {
    renderRow(
      <HomeRow
        kind="feature"
        testId="row-error-wrap"
        cells={CELLS}
        action={action({ error: "a fairly long error message that could otherwise widen the column" })}
      />,
    );
    expect(screen.getByRole("alert").className).toContain("break-words");
  });

  it("keeps header/body cell-count parity at nine cells whether or not an action is given", () => {
    renderRow(<HomeRow kind="project" testId="row-parity-noaction" cells={CELLS} />);
    expect(screen.getByTestId("row-parity-noaction").querySelectorAll("td")).toHaveLength(9);

    renderRow(<HomeRow kind="feature" testId="row-parity-action" cells={CELLS} action={action()} />);
    expect(screen.getByTestId("row-parity-action").querySelectorAll("td")).toHaveLength(9);
  });
});
