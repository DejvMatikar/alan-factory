import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useCallback } from "react";

import { matchShortcut } from "../../../lib/shortcuts";
import type { OperatorPort } from "../hooks/useOperatorSocket";
import { readOperatorTheme } from "../operatorTheme";

const TERMINAL_FONT_FAMILY = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
const TERMINAL_FONT_SIZE = 14;
const TERMINAL_SCROLLBACK = 5000;

export interface OperatorTerminalProps {
  readonly port: OperatorPort;
  readonly onEscape: () => void;
}

function handleTerminalKey(event: KeyboardEvent, onEscape: () => void): boolean {
  if (matchShortcut(event, ["operator-escape"]) === null) return true;
  if (event.type === "keydown") {
    event.preventDefault();
    onEscape();
  }
  return false;
}

function startTerminal(node: HTMLElement, port: OperatorPort, onEscape: () => void): () => void {
  const terminal = new Terminal({
    screenReaderMode: true,
    cursorBlink: true,
    fontFamily: TERMINAL_FONT_FAMILY,
    fontSize: TERMINAL_FONT_SIZE,
    scrollback: TERMINAL_SCROLLBACK,
    theme: readOperatorTheme(),
  });
  const fit = new FitAddon();
  terminal.loadAddon(fit);
  terminal.attachCustomKeyEventHandler((event) => handleTerminalKey(event, onEscape));
  terminal.open(node);

  const syncSize = (): void => {
    fit.fit();
    port.resize(terminal.cols, terminal.rows);
  };
  const dataListener = terminal.onData((data) => {
    port.send(data);
  });
  const binaryListener = terminal.onBinary((data) => {
    port.sendBinary(data);
  });
  const unbindOutput = port.bindOutput((chunk) => {
    terminal.write(chunk);
  });
  window.addEventListener("resize", syncSize);
  syncSize();
  terminal.focus();

  return () => {
    window.removeEventListener("resize", syncSize);
    unbindOutput();
    binaryListener.dispose();
    dataListener.dispose();
    terminal.dispose();
  };
}

export default function OperatorTerminal({ port, onEscape }: Readonly<OperatorTerminalProps>) {
  const mount = useCallback(
    (node: HTMLFieldSetElement) => startTerminal(node, port, onEscape),
    [port, onEscape],
  );
  return (
    <fieldset
      ref={mount}
      aria-label="Operator terminal"
      data-testid="operator-terminal"
      className="h-[65vh] min-h-64 w-full min-w-0 overflow-hidden rounded-md border border-border bg-surface p-2 focus-within:ring-2 focus-within:ring-text-primary/60"
    />
  );
}
