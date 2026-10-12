import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AggregateChip from "./AggregateChip";

describe("AggregateChip", () => {
  it("renders the needs-you label with its aria-hidden glyph, on the attention token", () => {
    render(<AggregateChip state="needs_you" testIdSuffix="repo-a" />);
    const chip = screen.getByTestId("aggregate-chip-repo-a");
    expect(chip).toHaveTextContent("needs you");
    expect(chip.className).toContain("status-attention");
    expect(chip.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });

  it("renders the executing label on the neutral token, since only needs-you carries chromatic treatment", () => {
    render(<AggregateChip state="executing" testIdSuffix="repo-b" />);
    const chip = screen.getByTestId("aggregate-chip-repo-b");
    expect(chip).toHaveTextContent("executing");
    expect(chip.className).toContain("status-neutral");
  });

  it("renders the idle label on the neutral token", () => {
    render(<AggregateChip state="idle" testIdSuffix="repo-c" />);
    const chip = screen.getByTestId("aggregate-chip-repo-c");
    expect(chip).toHaveTextContent("idle");
    expect(chip.className).toContain("status-neutral");
  });

  it("renders a legacy state's label as a bare text node with no tabular-nums wrapper", () => {
    render(<AggregateChip state="idle" testIdSuffix="repo-bare" />);
    const chip = screen.getByTestId("aggregate-chip-repo-bare");
    expect(chip.querySelector(".tabular-nums")).toBeNull();
  });

  it("renders the green tone on the success token with the given label and a tabular-nums wrapper", () => {
    render(<AggregateChip state="green" label="3 green · 0 red" testIdSuffix="repo-d" />);
    const chip = screen.getByTestId("aggregate-chip-repo-d");
    expect(chip).toHaveTextContent("3 green · 0 red");
    expect(chip.className).toContain("status-success");
    expect(chip.querySelector(".tabular-nums")).not.toBeNull();
  });

  it("renders the red tone on the danger token — reserved for confirmed act-now states", () => {
    render(<AggregateChip state="red" label="Budget paused" testIdSuffix="repo-e" />);
    const chip = screen.getByTestId("aggregate-chip-repo-e");
    expect(chip).toHaveTextContent("Budget paused");
    expect(chip.className).toContain("status-danger");
  });

  it("renders the transitional tone on the warning token for a stale-but-known value", () => {
    render(<AggregateChip state="transitional" label="2 green · 1 red · stale" testIdSuffix="repo-f" />);
    const chip = screen.getByTestId("aggregate-chip-repo-f");
    expect(chip).toHaveTextContent("2 green · 1 red · stale");
    expect(chip.className).toContain("status-warning");
  });

  it("renders the unknown tone as a neutral em-dash glyph with a label, never hidden", () => {
    render(<AggregateChip state="unknown" label="CI unknown" testIdSuffix="repo-g" />);
    const chip = screen.getByTestId("aggregate-chip-repo-g");
    expect(chip).toHaveTextContent("CI unknown");
    expect(chip).toHaveTextContent("—");
    expect(chip.className).toContain("status-neutral");
  });

  it("renders an empty label when a new-tone chip is given none", () => {
    render(<AggregateChip state="unknown" testIdSuffix="repo-h" />);
    const chip = screen.getByTestId("aggregate-chip-repo-h");
    expect(chip).toHaveTextContent("—");
  });
});
