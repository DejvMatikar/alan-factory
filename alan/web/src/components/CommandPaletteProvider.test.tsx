import { fireEvent, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { CommandPaletteProvider } from "./CommandPaletteProvider";
import { useCommandPalette, type CommandPaletteStep } from "./commandPaletteContext";

function OpenPaletteButton({ step, testId }: Readonly<{ step: CommandPaletteStep; testId: string }>) {
  const { openPalette } = useCommandPalette();
  return (
    <button type="button" data-testid={testId} onClick={() => openPalette(step)}>
      open
    </button>
  );
}

function renderProvider(children: ReactNode) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <CommandPaletteProvider>{children}</CommandPaletteProvider>
    </MemoryRouter>,
  );
}

describe("CommandPaletteProvider", () => {
  it("renders its children", () => {
    renderProvider(<div data-testid="child">hello</div>);
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("does not render the palette until a shortcut fires", () => {
    renderProvider(<div />);
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("Ctrl+K opens the palette at its root", () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "k", code: "KeyK", ctrlKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
    expect(screen.getByTestId("palette-command-build")).toBeInTheDocument();
  });

  it("Cmd+K (metaKey) also opens the palette", () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "k", code: "KeyK", metaKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
  });

  it("Ctrl+Alt+B opens the palette directly at the build-repo step", () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "B", code: "KeyB", ctrlKey: true, altKey: true });
    expect(screen.getByRole("heading", { name: "Build" })).toBeInTheDocument();
    expect(screen.getByTestId("palette-repo-DejvMatikar/alan")).toBeInTheDocument();
  });

  it("Ctrl+Alt+C opens the palette directly at the explore-consultant step", async () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "C", code: "KeyC", ctrlKey: true, altKey: true });
    expect(screen.getByRole("heading", { name: "Explore" })).toBeInTheDocument();
    expect(await screen.findByTestId("palette-consultant-research")).toBeInTheDocument();
  });

  it("Cmd+Alt+B (metaKey) also opens the palette at the build-repo step", () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "B", code: "KeyB", metaKey: true, altKey: true });
    expect(screen.getByRole("heading", { name: "Build" })).toBeInTheDocument();
  });

  it("Ctrl+Shift+X is left to the shell and does not open the palette", () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "X", code: "KeyX", ctrlKey: true, shiftKey: true });
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("Ctrl+Alt+K does not open the palette", () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "k", code: "KeyK", ctrlKey: true, altKey: true });
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("prevents the browser default for a shortcut it handles", () => {
    renderProvider(<div />);
    const notCancelled = fireEvent.keyDown(document, { key: "k", code: "KeyK", ctrlKey: true });
    expect(notCancelled).toBe(false);
  });

  it("leaves the browser default alone for a key it does not handle", () => {
    renderProvider(<div />);
    const notCancelled = fireEvent.keyDown(document, { key: "a", code: "KeyA", ctrlKey: true });
    expect(notCancelled).toBe(true);
  });

  it("lets a descendant open the palette at the root list through the context", async () => {
    renderProvider(<OpenPaletteButton step={{ name: "root" }} testId="opener" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("opener"));
    expect(screen.getByTestId("palette-command-build")).toBeInTheDocument();
  });

  it("lets a descendant open the palette directly at the build-repo step through the context", async () => {
    renderProvider(<OpenPaletteButton step={{ name: "build-repo" }} testId="opener" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("opener"));
    expect(screen.getByRole("heading", { name: "Build" })).toBeInTheDocument();
    expect(screen.getByTestId("palette-repo-DejvMatikar/alan")).toBeInTheDocument();
  });

  it("lets a descendant open the palette directly at the explore-consultant step through the context", async () => {
    renderProvider(<OpenPaletteButton step={{ name: "explore-consultant" }} testId="opener" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("opener"));
    expect(screen.getByRole("heading", { name: "Explore" })).toBeInTheDocument();
    expect(await screen.findByTestId("palette-consultant-research")).toBeInTheDocument();
  });

  it("keeps one stable context value across renders so consumers do not re-render", () => {
    const { result, rerender } = renderHook(() => useCommandPalette(), {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={["/"]}>
          <CommandPaletteProvider>{children}</CommandPaletteProvider>
        </MemoryRouter>
      ),
    });
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });

  it("gives a consumer outside any provider an open that does nothing", () => {
    const { result } = renderHook(() => useCommandPalette());
    expect(() => result.current.openPalette({ name: "root" })).not.toThrow();
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("plain K with no modifier does not open the palette", () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "k", code: "KeyK" });
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("a mod chord that maps to no shortcut does not open the palette", () => {
    renderProvider(<div />);
    fireEvent.keyDown(document, { key: "a", code: "KeyA", ctrlKey: true });
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("Ctrl+K does not open the palette while a text field is focused", () => {
    renderProvider(<input data-testid="text-field" />);
    screen.getByTestId("text-field").focus();
    fireEvent.keyDown(screen.getByTestId("text-field"), { key: "k", code: "KeyK", ctrlKey: true });
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("Ctrl+K does not open the palette while another dialog is open", () => {
    renderProvider(
      <dialog open data-testid="other-dialog">
        already open
      </dialog>,
    );
    fireEvent.keyDown(document, { key: "k", code: "KeyK", ctrlKey: true });
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("Escape closes the palette and restores focus to the previously focused control", async () => {
    renderProvider(<button type="button" data-testid="launcher">launch</button>);
    const launcher = screen.getByTestId("launcher");
    launcher.focus();
    expect(launcher).toHaveFocus();

    fireEvent.keyDown(document, { key: "k", code: "KeyK", ctrlKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
    expect(launcher).toHaveFocus();
  });
});
