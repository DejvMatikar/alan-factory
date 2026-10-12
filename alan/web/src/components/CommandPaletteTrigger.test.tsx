import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { CommandPaletteProvider } from "./CommandPaletteProvider";
import CommandPaletteTrigger from "./CommandPaletteTrigger";

const MAC_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";
const LINUX_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function stubUserAgent(userAgent: string): void {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);
}

function renderTrigger() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <CommandPaletteProvider>
        <CommandPaletteTrigger />
      </CommandPaletteProvider>
    </MemoryRouter>,
  );
}

describe("CommandPaletteTrigger", () => {
  it("is a text-labelled Commands button", () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    expect(screen.getByTestId("command-palette-trigger")).toHaveTextContent("Commands");
    expect(screen.getByRole("button", { name: /^Commands/ })).toBe(
      screen.getByTestId("command-palette-trigger"),
    );
  });

  it("is built from the shared ghost button, with its focus ring", () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const trigger = screen.getByTestId("command-palette-trigger");
    expect(trigger).toHaveAttribute("data-variant", "ghost");
    expect(trigger.className).toContain("focus-visible:ring-2");
  });

  it("appears at desktop widths only", () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const wrapper = screen.getByTestId("command-palette-trigger").closest("span.hidden");
    expect(wrapper).not.toBeNull();
    expect(wrapper?.className).toContain("sm:inline-flex");
  });

  it("shows Ctrl K and speaks Control K on a non-Apple platform", () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    expect(screen.getByText("Ctrl K")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "Commands , shortcut Control K" })).toBeInTheDocument();
  });

  it("shows ⌘K and speaks Command K on an Apple platform", () => {
    stubUserAgent(MAC_UA);
    renderTrigger();
    expect(screen.getByText("⌘K")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "Commands , shortcut Command K" })).toBeInTheDocument();
  });

  it("carries a plain-language description wired through aria-describedby", () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const describedBy = screen.getByTestId("command-palette-trigger").getAttribute("aria-describedby");
    expect(describedBy).toBe("command-palette-trigger-description");
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "Search and run commands, such as starting a build or exploring a repository.",
    );
  });

  it("never repeats the shortcut in its description", () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const description = document.getElementById("command-palette-trigger-description");
    expect(description?.textContent).not.toMatch(/ctrl|control|⌘|command k/i);
  });

  it("reveals the description on keyboard focus", async () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByTestId("command-palette-trigger")).toHaveFocus();
    expect(document.getElementById("command-palette-trigger-description")).not.toHaveClass("sr-only");
  });

  it("opens the palette at its root list when clicked", async () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("command-palette-trigger"));
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
    expect(screen.getByTestId("palette-command-build")).toBeInTheDocument();
    expect(screen.getByTestId("palette-command-explore")).toBeInTheDocument();
  });

  it("opens the palette from the keyboard with Enter", async () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const user = userEvent.setup();
    await user.tab();
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
  });

  it("opens the palette from the keyboard with Space", async () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const user = userEvent.setup();
    await user.tab();
    await user.keyboard(" ");
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
  });

  it("returns focus to the trigger when the palette closes on Escape", async () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("command-palette-trigger"));
    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
    expect(screen.getByTestId("command-palette-trigger")).toHaveFocus();
  });

  it("focuses itself before opening, so focus returns to it even when a click does not focus a button", () => {
    stubUserAgent(LINUX_UA);
    renderTrigger();
    const trigger = screen.getByTestId("command-palette-trigger");
    expect(trigger).not.toHaveFocus();
    fireEvent.click(trigger);
    fireEvent(screen.getByTestId("command-palette"), new Event("cancel"));
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
