import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  FRAME_PAD_REM,
  JUMP_TO_LATEST_CLASS,
  MAIN_PAD_TOP_REM,
  RAIL_MAX_H_CLASS,
  REVEAL_Z_CLASS,
  SCROLL_BODY_CLASS,
  SCROLL_BODY_FLOW_CLASS,
  SCROLL_BODY_SIZE_CLASS,
  SCROLL_COLUMN_CLASS,
  SCROLL_COLUMN_FLOW_CLASS,
  STICKY_FRAME_CHILD_TOP_CLASS,
  STICKY_REGION_CLASS,
  STICKY_REGION_Z_CLASS,
  STICKY_SLOT_TOP_CLASS,
  TABLE_HEADER_Z_CLASS,
  TOP_BAR_REM,
  TOP_BAR_Z_CLASS,
} from "./stickyOffsets";

const APP_PATH = path.resolve(process.cwd(), "src/App.tsx");
const HOVER_REVEAL_PATH = path.resolve(process.cwd(), "src/components/HoverReveal.tsx");
const REPO_WORKSPACE_TABLE_PATH = path.resolve(process.cwd(), "src/features/home/components/RepoWorkspaceTable.tsx");
const CHAT_PANEL_PATH = path.resolve(process.cwd(), "src/features/chat/components/ChatPanel.tsx");

function zIndexFromClass(cls: string): number {
  const match = /z-(\d+)/.exec(cls);
  if (match?.[1] === undefined) throw new Error(`no z-N token found in ${cls}`);
  return Number(match[1]);
}

function remFromTopClass(cls: string): number {
  const match = /sm:top-(\d+)/.exec(cls);
  if (match?.[1] === undefined) throw new Error(`no sm:top-N token found in ${cls}`);
  return Number(match[1]) * 0.25;
}

describe("stickyOffsets — slot offset", () => {
  it("equals the top-bar height plus main's own vertical padding", () => {
    expect(remFromTopClass(STICKY_SLOT_TOP_CLASS)).toBe(TOP_BAR_REM + MAIN_PAD_TOP_REM);
  });

  it("is not the top-bar height plus a single frame pad — the bug this module fixes", () => {
    expect(remFromTopClass(STICKY_SLOT_TOP_CLASS)).not.toBe(TOP_BAR_REM + FRAME_PAD_REM);
  });
});

describe("stickyOffsets — frame-child offset", () => {
  it("equals the slot offset plus one frame pad unit", () => {
    expect(remFromTopClass(STICKY_FRAME_CHILD_TOP_CLASS)).toBe(
      TOP_BAR_REM + MAIN_PAD_TOP_REM + FRAME_PAD_REM,
    );
  });
});

describe("stickyOffsets — rail max height", () => {
  it("subtracts the slot offset plus one frame pad unit from the viewport height", () => {
    const match = /calc\(100dvh-(\d+)rem\)/.exec(RAIL_MAX_H_CLASS);
    if (match?.[1] === undefined) throw new Error("RAIL_MAX_H_CLASS did not match the expected shape");
    expect(Number(match[1])).toBe(TOP_BAR_REM + MAIN_PAD_TOP_REM + FRAME_PAD_REM);
  });
});

describe("stickyOffsets — scroll column and body", () => {
  it("gates the scroll column at the sm: breakpoint, so nothing pins below it", () => {
    for (const token of SCROLL_COLUMN_CLASS.split(" ")) {
      expect(token.startsWith("sm:")).toBe(true);
    }
  });

  it("gates the scroll body at the sm: breakpoint, so nothing scrolls internally below it", () => {
    for (const token of SCROLL_BODY_CLASS.split(" ")) {
      expect(token.startsWith("sm:")).toBe(true);
    }
  });

  it("bounds the scroll column to the same max height as the rail", () => {
    expect(SCROLL_COLUMN_CLASS).toContain(RAIL_MAX_H_CLASS);
  });

  it("makes the scroll body the single flexible, overflow-scrolling child", () => {
    expect(SCROLL_BODY_CLASS).toContain("sm:flex-1");
    expect(SCROLL_BODY_CLASS).toContain("sm:overflow-y-auto");
    expect(SCROLL_BODY_CLASS).toContain("sm:min-h-0");
  });
});

describe("stickyOffsets — chat page-flow constants", () => {
  it("carry no max-h/overflow-y/min-h-0 token, unlike the dashboard's viewport-box constants", () => {
    for (const cls of [SCROLL_COLUMN_FLOW_CLASS, SCROLL_BODY_FLOW_CLASS]) {
      expect(cls).not.toMatch(/max-h/);
      expect(cls).not.toMatch(/overflow-y/);
      expect(cls).not.toMatch(/min-h-0/);
    }
  });

  it("pins the dashboard's viewport-box constants byte-identical, keeping the two modes from re-merging", () => {
    expect(SCROLL_COLUMN_CLASS).toBe(
      "sm:flex sm:min-h-0 sm:flex-col sm:max-h-[calc(100dvh-7rem)]",
    );
    expect(SCROLL_BODY_SIZE_CLASS).toBe("sm:min-h-0 sm:flex-1");
    expect(SCROLL_BODY_CLASS).toBe(`${SCROLL_BODY_SIZE_CLASS} sm:overflow-y-auto`);
  });
});

describe("stickyOffsets — sticky region", () => {
  it("pins at the frame-child offset", () => {
    expect(STICKY_REGION_CLASS).toContain(STICKY_FRAME_CHILD_TOP_CLASS);
  });

  it("folds STICKY_REGION_Z_CLASS into STICKY_REGION_CLASS", () => {
    expect(STICKY_REGION_CLASS).toContain(STICKY_REGION_Z_CLASS);
  });
});

describe("stickyOffsets — layering ladder", () => {
  it("orders the four layer constants strictly ascending: table header < sticky region < revealed description < top bar", () => {
    const ladder = [TABLE_HEADER_Z_CLASS, STICKY_REGION_Z_CLASS, REVEAL_Z_CLASS, TOP_BAR_Z_CLASS].map(
      zIndexFromClass,
    );
    for (let i = 0; i < ladder.length - 1; i += 1) {
      expect(ladder[i]).toBeLessThan(ladder[i + 1]!);
    }
  });

  it("has App.tsx reference TOP_BAR_Z_CLASS by name and carry no raw sm:z-N literal of its own", () => {
    const appSource = readFileSync(APP_PATH, "utf-8");
    expect(appSource).toContain("TOP_BAR_Z_CLASS");
    expect(appSource).not.toMatch(/sm:z-\d+/);
  });

  it("has HoverReveal.tsx reference REVEAL_Z_CLASS by name and carry no raw z-10 literal of its own", () => {
    const source = readFileSync(HOVER_REVEAL_PATH, "utf-8");
    expect(source).toContain("REVEAL_Z_CLASS");
    expect(source).not.toContain("z-10");
  });

  it("has RepoWorkspaceTable.tsx reference TABLE_HEADER_Z_CLASS by name", () => {
    const source = readFileSync(REPO_WORKSPACE_TABLE_PATH, "utf-8");
    expect(source).toContain("TABLE_HEADER_Z_CLASS");
  });
});

describe("stickyOffsets — jump-to-latest control", () => {
  it("carries no max-h/overflow-y token", () => {
    expect(JUMP_TO_LATEST_CLASS).not.toMatch(/max-h/);
    expect(JUMP_TO_LATEST_CLASS).not.toMatch(/overflow-y/);
  });

  it("sits below the global top bar in the layering ladder, as its own case", () => {
    expect(zIndexFromClass(JUMP_TO_LATEST_CLASS)).toBeLessThan(zIndexFromClass(TOP_BAR_Z_CLASS));
  });

  it("has ChatPanel.tsx reference JUMP_TO_LATEST_CLASS by name and carry no raw fixed/z-N literal", () => {
    const source = readFileSync(CHAT_PANEL_PATH, "utf-8");
    expect(source).toContain("JUMP_TO_LATEST_CLASS");
    expect(source).not.toMatch(/(?<!\w)fixed(?!\w)/);
    expect(source).not.toMatch(/z-\d+/);
  });
});
