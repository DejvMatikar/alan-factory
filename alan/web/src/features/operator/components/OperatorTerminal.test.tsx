import { render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FakeFitAddon, FakeTerminal } from "../../../test/fakeXterm";
import type { OperatorPort, OutputSink } from "../hooks/useOperatorSocket";
import OperatorTerminal from "./OperatorTerminal";

vi.mock("@xterm/xterm", async () => {
  const { FakeTerminal: Terminal } = await import("../../../test/fakeXterm");
  return { Terminal };
});

vi.mock("@xterm/addon-fit", async () => {
  const { FakeFitAddon: FitAddon } = await import("../../../test/fakeXterm");
  return { FitAddon };
});

interface PortHarness {
  readonly port: OperatorPort;
  readonly unbind: ReturnType<typeof vi.fn>;
  sink: () => OutputSink;
}

function makePort(): PortHarness {
  let bound: OutputSink | null = null;
  const unbind = vi.fn();
  const port: OperatorPort = {
    send: vi.fn(),
    sendBinary: vi.fn(),
    resize: vi.fn(),
    bindOutput: vi.fn((sink: OutputSink) => {
      bound = sink;
      return unbind;
    }),
  };
  return {
    port,
    unbind,
    sink: () => {
      if (bound === null) throw new Error("no sink bound");
      return bound;
    },
  };
}

function chord(type: string, overrides: KeyboardEventInit = {}): KeyboardEvent {
  return new KeyboardEvent(type, {
    code: "KeyF",
    ctrlKey: true,
    shiftKey: true,
    cancelable: true,
    ...overrides,
  });
}

beforeEach(() => {
  FakeTerminal.reset();
});

describe("OperatorTerminal — construction", () => {
  it("opens a terminal in its container with screen-reader mode on", () => {
    render(<OperatorTerminal port={makePort().port} onEscape={vi.fn()} />);
    const terminal = FakeTerminal.latest();
    expect(terminal.options.screenReaderMode).toBe(true);
    expect(terminal.host).toBe(screen.getByTestId("operator-terminal"));
  });

  it("names the container as the operator terminal for assistive technology", () => {
    render(<OperatorTerminal port={makePort().port} onEscape={vi.fn()} />);
    expect(screen.getByRole("group", { name: "Operator terminal" })).toBe(
      screen.getByTestId("operator-terminal"),
    );
  });

  it("applies the design-system theme read from the root element", () => {
    document.documentElement.style.setProperty("--color-surface", "#010203");
    render(<OperatorTerminal port={makePort().port} onEscape={vi.fn()} />);
    expect(FakeTerminal.latest().options.theme).toMatchObject({ background: "#010203" });
    document.documentElement.style.removeProperty("--color-surface");
  });

  it("fits the terminal and reports its size to the session on mount, then focuses it", () => {
    const { port } = makePort();
    render(<OperatorTerminal port={port} onEscape={vi.fn()} />);
    const terminal = FakeTerminal.latest();
    expect(FakeFitAddon.latest().fitCalls).toBe(1);
    expect(port.resize).toHaveBeenCalledExactlyOnceWith(80, 24);
    expect(terminal.focusCalls).toBe(1);
  });

  it("creates one terminal per mount even when the parent re-renders", () => {
    const { port } = makePort();
    const onEscape = vi.fn();
    const { rerender } = render(<OperatorTerminal port={port} onEscape={onEscape} />);
    rerender(<OperatorTerminal port={port} onEscape={onEscape} />);
    expect(FakeTerminal.instances).toHaveLength(1);
    expect(FakeTerminal.latest().disposed).toBe(false);
  });

  it("rebuilds the terminal when it is handed a different port", () => {
    const onEscape = vi.fn();
    const { rerender } = render(<OperatorTerminal port={makePort().port} onEscape={onEscape} />);
    const first = FakeTerminal.latest();
    rerender(<OperatorTerminal port={makePort().port} onEscape={onEscape} />);
    expect(first.disposed).toBe(true);
    expect(FakeTerminal.instances).toHaveLength(2);
  });

  it("survives StrictMode's mount, unmount, mount by leaving exactly one live terminal", () => {
    render(
      <StrictMode>
        <OperatorTerminal port={makePort().port} onEscape={vi.fn()} />
      </StrictMode>,
    );
    expect(FakeTerminal.instances.filter((terminal) => !terminal.disposed)).toHaveLength(1);
  });
});

describe("OperatorTerminal — data flow", () => {
  it("sends typed text to the session as keystrokes", () => {
    const { port } = makePort();
    render(<OperatorTerminal port={port} onEscape={vi.fn()} />);
    FakeTerminal.latest().typeData("ls\r");
    expect(port.send).toHaveBeenCalledExactlyOnceWith("ls\r");
  });

  it("sends binary input such as mouse reports to the session as raw bytes", () => {
    const { port } = makePort();
    render(<OperatorTerminal port={port} onEscape={vi.fn()} />);
    FakeTerminal.latest().typeBinary("\u001b[M");
    expect(port.sendBinary).toHaveBeenCalledExactlyOnceWith("\u001b[M");
  });

  it("writes session output into the terminal", () => {
    const harness = makePort();
    render(<OperatorTerminal port={harness.port} onEscape={vi.fn()} />);
    harness.sink()("hello");
    harness.sink()(new TextEncoder().encode(" world"));
    expect(FakeTerminal.latest().writtenText()).toBe("hello world");
  });
});

describe("OperatorTerminal — window resize", () => {
  it("fits again and reports the new size when the window resizes", () => {
    const { port } = makePort();
    render(<OperatorTerminal port={port} onEscape={vi.fn()} />);
    FakeFitAddon.latest().nextSize = { cols: 132, rows: 43 };
    window.dispatchEvent(new Event("resize"));
    expect(FakeFitAddon.latest().fitCalls).toBe(2);
    expect(port.resize).toHaveBeenLastCalledWith(132, 43);
    expect(port.resize).toHaveBeenCalledTimes(2);
  });

  it("stops reacting to window resizes after unmount", () => {
    const { port } = makePort();
    const { unmount } = render(<OperatorTerminal port={port} onEscape={vi.fn()} />);
    unmount();
    window.dispatchEvent(new Event("resize"));
    expect(FakeFitAddon.latest().fitCalls).toBe(1);
    expect(port.resize).toHaveBeenCalledTimes(1);
  });
});

describe("OperatorTerminal — teardown", () => {
  it("disposes the terminal, its listeners and its output binding on unmount", () => {
    const harness = makePort();
    const { unmount } = render(<OperatorTerminal port={harness.port} onEscape={vi.fn()} />);
    const terminal = FakeTerminal.latest();
    unmount();
    expect(terminal.disposed).toBe(true);
    expect(harness.unbind).toHaveBeenCalledTimes(1);
    terminal.typeData("late");
    terminal.typeBinary("late");
    expect(harness.port.send).not.toHaveBeenCalled();
    expect(harness.port.sendBinary).not.toHaveBeenCalled();
  });
});

describe("OperatorTerminal — the escape shortcut", () => {
  it("moves focus out on Ctrl+Shift+F, prevents the default and keeps xterm from seeing it", () => {
    const onEscape = vi.fn();
    render(<OperatorTerminal port={makePort().port} onEscape={onEscape} />);
    const event = chord("keydown");
    expect(FakeTerminal.latest().pressKey(event)).toBe(false);
    expect(event.defaultPrevented).toBe(true);
    expect(onEscape).toHaveBeenCalledTimes(1);
  });

  it("swallows the matching keyup and keypress without acting on them", () => {
    const onEscape = vi.fn();
    render(<OperatorTerminal port={makePort().port} onEscape={onEscape} />);
    for (const type of ["keyup", "keypress"]) {
      const event = chord(type);
      expect(FakeTerminal.latest().pressKey(event)).toBe(false);
      expect(event.defaultPrevented).toBe(false);
    }
    expect(onEscape).not.toHaveBeenCalled();
  });

  it.each([
    ["plain F", { ctrlKey: false, shiftKey: false }],
    ["Ctrl+F", { shiftKey: false }],
    ["Shift+F", { ctrlKey: false }],
    ["Ctrl+Shift+G", { code: "KeyG" }],
    ["Escape", { code: "Escape", ctrlKey: false, shiftKey: false }],
  ] as const)("hands %s through to Claude Code untouched", (_label, overrides) => {
    const onEscape = vi.fn();
    render(<OperatorTerminal port={makePort().port} onEscape={onEscape} />);
    const event = chord("keydown", overrides);
    expect(FakeTerminal.latest().pressKey(event)).toBe(true);
    expect(event.defaultPrevented).toBe(false);
    expect(onEscape).not.toHaveBeenCalled();
  });

  it("is handled only inside the terminal: the chord on the document does nothing", () => {
    const onEscape = vi.fn();
    render(<OperatorTerminal port={makePort().port} onEscape={onEscape} />);
    const event = chord("keydown");
    document.dispatchEvent(event);
    document.body.dispatchEvent(event);
    expect(onEscape).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });
});
