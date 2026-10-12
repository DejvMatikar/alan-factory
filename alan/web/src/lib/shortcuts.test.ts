import { describe, expect, it, vi } from "vitest";

import { matchShortcut, shortcutHint, type ShortcutId } from "./shortcuts";

const MAC_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";
const LINUX_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const ALL_IDS: readonly ShortcutId[] = [
  "palette",
  "build-direct",
  "explore-direct",
  "build-repo",
  "explore-repo",
  "modal-confirm",
  "operator-escape",
];

interface ReservedCombo {
  readonly label: string;
  readonly code: string;
  readonly modifiers: KeyboardEventInit;
}

const CTRL = { ctrlKey: true };
const CTRL_SHIFT = { ctrlKey: true, shiftKey: true };
const META = { metaKey: true };
const META_SHIFT = { metaKey: true, shiftKey: true };
const CTRL_ALT = { ctrlKey: true, altKey: true };

const BROWSER_RESERVED: readonly ReservedCombo[] = [
  { label: "Ctrl+T new tab", code: "KeyT", modifiers: CTRL },
  { label: "Ctrl+W close tab", code: "KeyW", modifiers: CTRL },
  { label: "Ctrl+N new window", code: "KeyN", modifiers: CTRL },
  { label: "Ctrl+Shift+T reopen tab", code: "KeyT", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Shift+W close window", code: "KeyW", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Shift+N private window", code: "KeyN", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Shift+P private window (Firefox)", code: "KeyP", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Tab next tab", code: "Tab", modifiers: CTRL },
  { label: "Ctrl+Shift+Tab previous tab", code: "Tab", modifiers: CTRL_SHIFT },
  { label: "Ctrl+PageUp previous tab", code: "PageUp", modifiers: CTRL },
  { label: "Ctrl+PageDown next tab", code: "PageDown", modifiers: CTRL },
  { label: "Ctrl+L address bar", code: "KeyL", modifiers: CTRL },
  { label: "Ctrl+R reload", code: "KeyR", modifiers: CTRL },
  { label: "Ctrl+Shift+R hard reload", code: "KeyR", modifiers: CTRL_SHIFT },
  { label: "Ctrl+F find", code: "KeyF", modifiers: CTRL },
  { label: "Ctrl+G find next", code: "KeyG", modifiers: CTRL },
  { label: "Ctrl+P print", code: "KeyP", modifiers: CTRL },
  { label: "Ctrl+S save page", code: "KeyS", modifiers: CTRL },
  { label: "Ctrl+D bookmark", code: "KeyD", modifiers: CTRL },
  { label: "Ctrl+H history", code: "KeyH", modifiers: CTRL },
  { label: "Ctrl+J downloads", code: "KeyJ", modifiers: CTRL },
  { label: "Ctrl+U view source", code: "KeyU", modifiers: CTRL },
  { label: "Ctrl+Shift+I developer tools", code: "KeyI", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Shift+J console", code: "KeyJ", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Shift+C inspect element", code: "KeyC", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Shift+B bookmarks bar", code: "KeyB", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Shift+O bookmark manager", code: "KeyO", modifiers: CTRL_SHIFT },
  { label: "Ctrl+Shift+Delete clear data", code: "Delete", modifiers: CTRL_SHIFT },
  { label: "Cmd+Q quit", code: "KeyQ", modifiers: META },
  { label: "Cmd+W close tab", code: "KeyW", modifiers: META },
  { label: "Cmd+T new tab", code: "KeyT", modifiers: META },
  { label: "Cmd+N new window", code: "KeyN", modifiers: META },
  { label: "Cmd+L address bar", code: "KeyL", modifiers: META },
  { label: "Cmd+R reload", code: "KeyR", modifiers: META },
  { label: "Cmd+F find", code: "KeyF", modifiers: META },
  { label: "Cmd+Shift+F find in page (Safari)", code: "KeyF", modifiers: META_SHIFT },
];

const TERMINAL_CLASHES: readonly ReservedCombo[] = [
  { label: "Ctrl+C interrupt", code: "KeyC", modifiers: CTRL },
  { label: "Ctrl+D end of input", code: "KeyD", modifiers: CTRL },
  { label: "Ctrl+B tmux prefix", code: "KeyB", modifiers: CTRL },
  { label: "Ctrl+O toggle transcript", code: "KeyO", modifiers: CTRL },
  { label: "Ctrl+A line start", code: "KeyA", modifiers: CTRL },
  { label: "Ctrl+E line end", code: "KeyE", modifiers: CTRL },
  { label: "Ctrl+K kill to end of line", code: "KeyK", modifiers: CTRL },
  { label: "Ctrl+U kill to line start", code: "KeyU", modifiers: CTRL },
  { label: "Ctrl+Y yank", code: "KeyY", modifiers: CTRL },
  { label: "Ctrl+Z suspend", code: "KeyZ", modifiers: CTRL },
  { label: "Ctrl+V paste image", code: "KeyV", modifiers: CTRL },
  { label: "Ctrl+Alt+F word forward", code: "KeyF", modifiers: CTRL_ALT },
  { label: "Shift+Tab cycle mode", code: "Tab", modifiers: { shiftKey: true } },
  { label: "Escape interrupt", code: "Escape", modifiers: {} },
  { label: "Enter submit", code: "Enter", modifiers: {} },
];

const APPLE_HINTS: readonly [ShortcutId, string, string][] = [
  ["palette", "⌘K", "Command K"],
  ["build-direct", "⌥⌘B", "Option Command B"],
  ["explore-direct", "⌥⌘C", "Option Command C"],
  ["build-repo", "⌃⇧X", "Control Shift X"],
  ["explore-repo", "⌃⇧E", "Control Shift E"],
  ["modal-confirm", "⌃↵", "Control Enter"],
  ["operator-escape", "⌃⇧F", "Control Shift F"],
];

const OTHER_HINTS: readonly [ShortcutId, string, string][] = [
  ["palette", "Ctrl K", "Control K"],
  ["build-direct", "Ctrl Alt B", "Control Alt B"],
  ["explore-direct", "Ctrl Alt C", "Control Alt C"],
  ["build-repo", "Ctrl Shift X", "Control Shift X"],
  ["explore-repo", "Ctrl Shift E", "Control Shift E"],
  ["modal-confirm", "Ctrl Enter", "Control Enter"],
  ["operator-escape", "Ctrl Shift F", "Control Shift F"],
];

function keydown(code: string, modifiers: KeyboardEventInit = {}): KeyboardEvent {
  return new KeyboardEvent("keydown", { code, ...modifiers });
}

describe("shortcutHint", () => {
  it.each(APPLE_HINTS)("shows %s as %s and speaks %s on an Apple platform", (id, glyph, spoken) => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(MAC_UA);
    expect(shortcutHint(id)).toEqual({ glyph, spoken });
  });

  it.each(OTHER_HINTS)("shows %s as %s and speaks %s on any other platform", (id, glyph, spoken) => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(LINUX_UA);
    expect(shortcutHint(id)).toEqual({ glyph, spoken });
  });

  it("re-reads the platform on every call", () => {
    const userAgent = vi.spyOn(navigator, "userAgent", "get");
    userAgent.mockReturnValue(LINUX_UA);
    expect(shortcutHint("palette").glyph).toBe("Ctrl K");
    userAgent.mockReturnValue(MAC_UA);
    expect(shortcutHint("palette").glyph).toBe("⌘K");
  });
});

describe("matchShortcut — palette", () => {
  it("matches Ctrl+K", () => {
    expect(matchShortcut(keydown("KeyK", { ctrlKey: true }), ALL_IDS)).toBe("palette");
  });

  it("matches Meta+K", () => {
    expect(matchShortcut(keydown("KeyK", { metaKey: true }), ALL_IDS)).toBe("palette");
  });

  it("does not match K with Alt held", () => {
    expect(matchShortcut(keydown("KeyK", { ctrlKey: true, altKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match K with no modifier", () => {
    expect(matchShortcut(keydown("KeyK"), ALL_IDS)).toBeNull();
  });

  it("does not match another key under Ctrl", () => {
    expect(matchShortcut(keydown("KeyA", { ctrlKey: true }), ALL_IDS)).toBeNull();
  });
});

describe("matchShortcut — direct shortcuts", () => {
  it("matches Ctrl+Alt+B as the direct build shortcut", () => {
    expect(matchShortcut(keydown("KeyB", { ctrlKey: true, altKey: true }), ALL_IDS)).toBe("build-direct");
  });

  it("matches Meta+Alt+B as the direct build shortcut", () => {
    expect(matchShortcut(keydown("KeyB", { metaKey: true, altKey: true }), ALL_IDS)).toBe("build-direct");
  });

  it("does not match B without Alt", () => {
    expect(matchShortcut(keydown("KeyB", { ctrlKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match Alt+B without Ctrl or Meta", () => {
    expect(matchShortcut(keydown("KeyB", { altKey: true }), ALL_IDS)).toBeNull();
  });

  it("matches Ctrl+Alt+C as the direct explore shortcut", () => {
    expect(matchShortcut(keydown("KeyC", { ctrlKey: true, altKey: true }), ALL_IDS)).toBe("explore-direct");
  });

  it("matches Meta+Alt+C as the direct explore shortcut", () => {
    expect(matchShortcut(keydown("KeyC", { metaKey: true, altKey: true }), ALL_IDS)).toBe("explore-direct");
  });

  it("does not match C without Alt", () => {
    expect(matchShortcut(keydown("KeyC", { ctrlKey: true }), ALL_IDS)).toBeNull();
  });
});

describe("matchShortcut — repository shortcuts", () => {
  it("matches Ctrl+Shift+X as the repository build shortcut", () => {
    expect(matchShortcut(keydown("KeyX", { ctrlKey: true, shiftKey: true }), ALL_IDS)).toBe("build-repo");
  });

  it("matches Ctrl+Shift+E as the repository explore shortcut", () => {
    expect(matchShortcut(keydown("KeyE", { ctrlKey: true, shiftKey: true }), ALL_IDS)).toBe("explore-repo");
  });

  it("ignores Alt and Meta alongside Ctrl+Shift", () => {
    const event = keydown("KeyX", { ctrlKey: true, shiftKey: true, altKey: true, metaKey: true });
    expect(matchShortcut(event, ALL_IDS)).toBe("build-repo");
  });

  it("does not match X without Shift", () => {
    expect(matchShortcut(keydown("KeyX", { ctrlKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match X without Ctrl", () => {
    expect(matchShortcut(keydown("KeyX", { shiftKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match Meta+Shift+X, which the repository shortcuts never claimed", () => {
    expect(matchShortcut(keydown("KeyX", { metaKey: true, shiftKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match E without Shift", () => {
    expect(matchShortcut(keydown("KeyE", { ctrlKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match another key under Ctrl+Shift", () => {
    expect(matchShortcut(keydown("KeyZ", { ctrlKey: true, shiftKey: true }), ALL_IDS)).toBeNull();
  });
});

describe("matchShortcut — modal confirm", () => {
  it("matches Ctrl+Enter", () => {
    expect(matchShortcut(keydown("Enter", { ctrlKey: true, key: "Enter" }), ALL_IDS)).toBe(
      "modal-confirm",
    );
  });

  it("matches Ctrl+Shift+Enter, parity with the modal's prior bare Ctrl+Enter branch", () => {
    expect(
      matchShortcut(keydown("Enter", { ctrlKey: true, shiftKey: true, key: "Enter" }), ALL_IDS),
    ).toBe("modal-confirm");
  });

  it("does not match Meta+Enter", () => {
    expect(matchShortcut(keydown("Enter", { metaKey: true, key: "Enter" }), ALL_IDS)).toBeNull();
  });

  it("does not match bare Enter with no modifier", () => {
    expect(matchShortcut(keydown("Enter", { key: "Enter" }), ALL_IDS)).toBeNull();
  });

  it("does not match Ctrl+K", () => {
    expect(
      matchShortcut(keydown("KeyK", { ctrlKey: true, key: "k" }), ["modal-confirm"]),
    ).toBeNull();
  });
});

describe("matchShortcut — operator escape", () => {
  it("matches Ctrl+Shift+F", () => {
    expect(matchShortcut(keydown("KeyF", { ctrlKey: true, shiftKey: true }), ALL_IDS)).toBe(
      "operator-escape",
    );
  });

  it("does not match F without Shift", () => {
    expect(matchShortcut(keydown("KeyF", { ctrlKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match F without Ctrl", () => {
    expect(matchShortcut(keydown("KeyF", { shiftKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match Meta+Shift+F", () => {
    expect(matchShortcut(keydown("KeyF", { metaKey: true, shiftKey: true }), ALL_IDS)).toBeNull();
  });

  it("does not match another key under Ctrl+Shift", () => {
    expect(matchShortcut(keydown("KeyG", { ctrlKey: true, shiftKey: true }), ["operator-escape"])).toBeNull();
  });
});

describe("operator escape against browser-reserved and terminal combos", () => {
  it.each(BROWSER_RESERVED)("does not claim the browser-reserved $label", ({ code, modifiers }) => {
    expect(matchShortcut(keydown(code, modifiers), ["operator-escape"])).toBeNull();
  });

  it.each(TERMINAL_CLASHES)("does not claim the Claude Code or tmux key $label", ({ code, modifiers }) => {
    expect(matchShortcut(keydown(code, modifiers), ["operator-escape"])).toBeNull();
  });

  it("keeps its own Ctrl+Shift+F chord off both denylists", () => {
    const listed = [...BROWSER_RESERVED, ...TERMINAL_CLASHES].filter(
      ({ code, modifiers }) =>
        code === "KeyF" && modifiers.ctrlKey === true && modifiers.shiftKey === true && modifiers.altKey !== true,
    );
    expect(listed).toEqual([]);
  });
});

describe("matchShortcut — candidate list", () => {
  it("matches only within the ids it is given", () => {
    const event = keydown("KeyX", { ctrlKey: true, shiftKey: true });
    expect(matchShortcut(event, ["palette", "build-direct"])).toBeNull();
  });

  it("matches nothing against an empty list", () => {
    expect(matchShortcut(keydown("KeyK", { ctrlKey: true }), [])).toBeNull();
  });
});
