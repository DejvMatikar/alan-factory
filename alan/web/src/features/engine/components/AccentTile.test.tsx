import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ACCENT_COLORS } from "../../../api";
import AccentTile, { accentColorLabel } from "./AccentTile";

describe("accentColorLabel", () => {
  it("names every curated colour", () => {
    expect(accentColorLabel("iris")).toBe("Iris");
    expect(accentColorLabel("violet")).toBe("Violet");
    expect(accentColorLabel("plum")).toBe("Plum");
    expect(accentColorLabel("orchid")).toBe("Orchid");
    expect(accentColorLabel("magenta")).toBe("Magenta");
    expect(accentColorLabel("rose")).toBe("Rose");
    expect(accentColorLabel("ruby")).toBe("Ruby");
    expect(accentColorLabel("crimson")).toBe("Crimson");
    expect(accentColorLabel("orange")).toBe("Orange");
    expect(accentColorLabel("amber")).toBe("Amber");
    expect(accentColorLabel("tan")).toBe("Tan");
    expect(accentColorLabel("green")).toBe("Green");
    expect(accentColorLabel("teal")).toBe("Teal");
    expect(accentColorLabel("blue")).toBe("Blue");
    expect(accentColorLabel("slate")).toBe("Slate");
    expect(accentColorLabel("bone")).toBe("Bone");
  });

  it("names the neutral no-accent option", () => {
    expect(accentColorLabel(null)).toBe("No accent");
  });

  it("returns a non-empty, distinct label for every colour in the palette", () => {
    const labels = ACCENT_COLORS.map((color) => accentColorLabel(color));
    for (const label of labels) {
      expect(label.length).toBeGreaterThan(0);
    }
    expect(new Set(labels).size).toBe(ACCENT_COLORS.length);
  });
});

describe("AccentTile", () => {
  it("exposes the colour name as its accessible name, not just its appearance", () => {
    render(<AccentTile color="iris" selected={false} taken={false} onSelect={vi.fn()} />);
    expect(screen.getByRole("radio", { name: "Iris" })).toBeInTheDocument();
  });

  it("renders the neutral no-accent swatch with its own glyph and accessible name", () => {
    render(<AccentTile color={null} selected={false} taken={false} onSelect={vi.fn()} />);
    expect(screen.getByRole("radio", { name: "No accent" })).toBeInTheDocument();
  });

  it("marks a selected tile with a check glyph in addition to aria-checked", () => {
    render(<AccentTile color="ruby" selected taken={false} onSelect={vi.fn()} />);
    const tile = screen.getByRole("radio", { name: "Ruby" });
    expect(tile).toHaveAttribute("aria-checked", "true");
    expect(tile).toHaveAttribute("data-selected", "true");
    expect(screen.getByTestId("accent-tile-ruby-check")).toBeInTheDocument();
  });

  it("renders no check glyph and aria-checked=false when unselected", () => {
    render(<AccentTile color="ruby" selected={false} taken={false} onSelect={vi.fn()} />);
    const tile = screen.getByRole("radio", { name: "Ruby" });
    expect(tile).toHaveAttribute("aria-checked", "false");
    expect(tile).toHaveAttribute("data-selected", "false");
    expect(screen.queryByTestId("accent-tile-ruby-check")).not.toBeInTheDocument();
  });

  it("marks a taken, unselected colour in its accessible name and with a taken indicator", () => {
    render(<AccentTile color="plum" selected={false} taken onSelect={vi.fn()} />);
    const tile = screen.getByRole("radio", { name: "Plum — already used by another repository" });
    expect(tile).toHaveAttribute("data-taken", "true");
    expect(screen.getByTestId("accent-tile-plum-taken")).toBeInTheDocument();
  });

  it("prefers the selected check over the taken indicator when a repository selects an already-claimed colour", () => {
    render(<AccentTile color="plum" selected taken onSelect={vi.fn()} />);
    expect(screen.getByTestId("accent-tile-plum-check")).toBeInTheDocument();
    expect(screen.queryByTestId("accent-tile-plum-taken")).not.toBeInTheDocument();
  });

  it("calls onSelect when clicked", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<AccentTile color="violet" selected={false} taken={false} onSelect={onSelect} />);
    await user.click(screen.getByRole("radio", { name: "Violet" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("activates via the keyboard when focused", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<AccentTile color="violet" selected={false} taken={false} onSelect={onSelect} />);
    const tile = screen.getByRole("radio", { name: "Violet" });
    tile.focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("disables the tile and blocks selection when disabled", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<AccentTile color="violet" selected={false} taken={false} disabled onSelect={onSelect} />);
    const tile = screen.getByRole("radio", { name: "Violet" });
    expect(tile).toBeDisabled();
    await user.click(tile);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
