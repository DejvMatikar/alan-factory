import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const CSS_PATH = path.resolve(process.cwd(), "src/index.css");
const DOC_PATH = path.resolve(process.cwd(), "../../docs/DESIGN_SYSTEM.md");
const UX_RULES_PATH = path.resolve(process.cwd(), "../../docs/UX_AUTHORING_RULES.md");
const SRC_DIR = path.resolve(process.cwd(), "src");
const COLOR_TOKEN_PATTERN = /(--color-[\w-]+):\s*(#[0-9a-fA-F]{3,8}|oklch\([^)]*\))/g;
const TEXT_SIZE_PATTERN = /\btext-(?:xs|sm|base|lg|xl|2xl)\b/g;
const TYPOGRAPHY_SCALE = ["text-xs", "text-sm", "text-base", "text-lg", "text-xl", "text-2xl"];

const RATIFIED_RECIPES = [
  { label: "badge", fragment: "inline-block rounded px-2 py-0.5 text-xs" },
  {
    label: "nav-link chrome",
    fragment:
      "inline-flex min-h-11 min-w-11 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-text-primary hover:bg-surface-elevated",
  },
  { label: "block-level error banner", fragment: "rounded-md bg-banner-error-tint/15 px-3 py-2" },
  { label: "field-level destructive-text token", fragment: "text-btn-destroy" },
  { label: "body link", fragment: "text-btn-action underline" },
  { label: "accent selection edge", fragment: "border-[var(--accent-edge)]" },
  { label: "accent frame glow, left edge", fragment: "shadow-[-6px_0_18px_var(--accent-glow)]" },
  { label: "accent frame glow, right edge", fragment: "shadow-[6px_0_18px_var(--accent-glow)]" },
  {
    label: "shell selection interactive state",
    fragment:
      "hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60",
  },
  {
    label: "sticky page region",
    fragment: "sm:sticky sm:top-28 bg-surface sm:z-20",
  },
];

const CHARTER_DIMENSIONS = [
  "Badge / status-pill recipe",
  "Tab/toggle presentation",
  "Nav-link chrome",
  "Icon-button affordance",
  "Two-tier error colour rule",
  "Force-ship variant",
  "Typography",
];

function readFlatDoc(file: string): string {
  return readFileSync(file, "utf-8").replace(/\s+/g, " ");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function isNonTestSourceFile(name: string): boolean {
  return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name);
}

function collectSourceText(dir: string): string {
  let combined = "";
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !isNonTestSourceFile(entry.name)) continue;
    combined += readFileSync(path.join(entry.parentPath, entry.name), "utf-8");
  }
  return combined;
}

describe("design-system drift guard", () => {
  it("keeps every --color-* token's name and value verbatim in the same DESIGN_SYSTEM.md table row", () => {
    const css = readFileSync(CSS_PATH, "utf-8");
    const doc = readFileSync(DOC_PATH, "utf-8");
    const tokens = [...css.matchAll(COLOR_TOKEN_PATTERN)];

    expect(tokens.length).toBeGreaterThan(0);
    for (const [, name, value] of tokens) {
      expect(name).toBeDefined();
      expect(value).toBeDefined();
      const rowPattern = new RegExp(
        `\\|\\s*\`${escapeRegExp(name ?? "")}\`\\s*\\|\\s*\`${escapeRegExp(value ?? "")}\`\\s*\\|`,
      );
      expect(doc).toMatch(rowPattern);
    }
  });

  it("keeps the ratified typography scale exactly in sync with the text-* classes used in source", () => {
    const source = collectSourceText(SRC_DIR);
    const used = new Set([...source.matchAll(TEXT_SIZE_PATTERN)].map((m) => m[0]));

    expect([...used].sort()).toEqual([...TYPOGRAPHY_SCALE].sort());
  });

  it("documents every ratified typography step in DESIGN_SYSTEM.md's scale table", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    for (const step of TYPOGRAPHY_SCALE) {
      expect(doc).toContain(`\`${step}\``);
    }
  });

  it("no longer claims there is no custom typography scale", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).not.toContain("no custom typography scale");
  });

  it("no longer claims the accent palette sits clear of every status/button/scope hue", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).not.toContain(
      "sit clear of every `--color-btn-*`/`--color-status-*`/`--color-scope-repo-*`",
    );
  });

  it("no longer claims the frame slots pad only left and right", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).not.toContain("Both frame-line slots additionally carry `pl-4`/`pr-4`");
  });

  it("documents and implements the frame top-pad and strong-selection-tier constants", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");
    const source = collectSourceText(SRC_DIR);

    expect(doc).toContain("ACCENT_FRAME_PAD_TOP_CLASS");
    expect(source).toContain("ACCENT_FRAME_PAD_TOP_CLASS");
    expect(doc).toContain("SELECTION_ROW_SELECTED_CLASS");
    expect(source).toContain("SELECTION_ROW_SELECTED_CLASS");
  });

  it("keeps the disabled treatment uniform: disabled:opacity-50 is the only disabled:opacity-* value in source", () => {
    const source = collectSourceText(SRC_DIR);
    const values = new Set([...source.matchAll(/disabled:opacity-\d+/g)].map((m) => m[0]));

    expect([...values]).toEqual(["disabled:opacity-50"]);
  });

  it("keeps the ring-text-primary/60 focus recipe present under all three documented prefixes", () => {
    const source = collectSourceText(SRC_DIR);

    expect(source).toContain("focus-visible:ring-text-primary/60");
    expect(source).toContain("focus:ring-text-primary/60");
    expect(source).toContain("focus-within:ring-text-primary/60");
  });

  it.each(RATIFIED_RECIPES)(
    "keeps the ratified $label recipe quoted in DESIGN_SYSTEM.md and present in source",
    ({ fragment }) => {
      const doc = readFileSync(DOC_PATH, "utf-8");
      const source = collectSourceText(SRC_DIR);

      expect(doc).toContain(fragment);
      expect(source).toContain(fragment);
    },
  );

  it("widens the colour-token pattern to actually capture oklch(...) tokens", () => {
    const css = readFileSync(CSS_PATH, "utf-8");
    const tokens = [...css.matchAll(COLOR_TOKEN_PATTERN)];

    expect(tokens.some(([, , value]) => value?.startsWith("oklch("))).toBe(true);
  });

  it("keeps no non-test source file using the raw banner-recipe fragment", () => {
    const source = collectSourceText(SRC_DIR);

    expect(source).not.toContain("rounded-md bg-red-500/15 px-3 py-2");
  });

  it("no longer names the retired HiddenRepoDisclosure component", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).not.toContain("HiddenRepoDisclosure.tsx");
  });

  it("no longer names the retired WorkspaceChatPanel component", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).not.toContain("WorkspaceChatPanel.tsx");
  });

  it("no longer names the retired DecisionForm component", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).not.toContain("DecisionForm.tsx");
  });

  it("names HiddenRepoDialog.tsx as the hidden-repositories surface", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).toContain("components/HiddenRepoDialog.tsx");
  });

  it("names ProjectFeaturesToggle.tsx as the top-bar project-features surface, ordered before the hidden-repositories trigger", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).toContain("components/ProjectFeaturesToggle.tsx");
    expect(doc).toContain("Projects · Hidden (N) · Health · Settings");
  });

  it("ratifies unresolved posture as a first-class aria-disabled state, never a collapse into the gated default", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).toContain("never-render-an-unresolved-posture-as-a-real-one");
    expect(doc).toContain("aria-disabled");
  });

  it("states the drift-guarded vs convention-prose governance boundary", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).toContain("Governance line");
    expect(doc).toContain("convention prose");
  });

  it("records the 24px graph-card control exception under Spacing & tap targets, backed by the compact Button", () => {
    const doc = readFlatDoc(DOC_PATH);
    const section = doc.slice(
      doc.indexOf("## Spacing & tap targets"),
      doc.indexOf("## Typography"),
    );

    expect(section).toContain("graph-canvas node cards");
    expect(section).toContain('density="compact"');
    expect(section).toContain("24px minimum target");
    expect(section).toContain("WCAG 2.5.8");
    expect(collectSourceText(SRC_DIR)).toContain("min-h-6 min-w-6");
  });

  it("documents how a step card signals it is clickable, with focus distinct from selection", () => {
    const doc = readFlatDoc(DOC_PATH);
    const section = doc.slice(
      doc.indexOf("## Interaction states"),
      doc.indexOf("## Accessibility baseline"),
    );

    expect(section).toContain("`StepNodeCard`");
    expect(section).toContain("trailing chevron");
    expect(section).toContain("`cursor-pointer`");
    expect(section).toContain('`aria-current="true"`');
    expect(section).toContain("`ring-2 ring-text-primary/60`");
    expect(section).toContain("border-l-4");
    expect(section).toContain("keyboard focus and selection stay visually distinct");
    expect(section).toContain("no `title` attribute");
  });

  it("states that the run full-view page is user-facing and linked from each run node", () => {
    const doc = readFlatDoc(DOC_PATH);

    expect(doc).toContain("`/workspaces/:taskId/invocations/:invId`");
    expect(doc).toContain("is a user-facing page");
    expect(doc).toContain('a visible "Open full view" link');
    expect(doc).toContain("`InvGroupNode`");
  });

  it("names the step-detail pop-up's modal-key convention and icon-only close control", () => {
    const doc = readFlatDoc(DOC_PATH);

    expect(doc).toContain("`NodeDetailModal.tsx` follows the standard modal-key convention");
    expect(doc).toContain("`HoverReveal` description with an `Esc` `ShortcutHint`");
  });

  it("names UnderlineTab.tsx and tablistKeys.ts as shared primitives that are not the app-wide standard", () => {
    const doc = readFlatDoc(DOC_PATH);

    expect(doc).toContain("components/UnderlineTab.tsx");
    expect(doc).toContain("components/tablistKeys.ts");
    expect(doc).toContain("shared primitives that any tablist may reuse");
    expect(doc).toContain("not ratified as the app-wide standard");
    expect(doc).toContain("the pill-vs-underline decision stays open");
  });

  it("raises a hovered or focused step card above its neighbours for both graph node types that render it", () => {
    const css = readFileSync(CSS_PATH, "utf-8");
    const source = collectSourceText(SRC_DIR);

    for (const nodeType of ["step", "llm"]) {
      expect(css).toContain(`.react-flow__node-${nodeType}:hover`);
      expect(css).toContain(`.react-flow__node-${nodeType}:focus-within`);
    }
    expect(css).toContain("z-index: 1000 !important");
    expect(source).toContain("step: StepNodeWrapper");
    expect(source).toContain("llm: CustomNode");
  });

  it("no longer names the retired ChatTabButton.tsx in DESIGN_SYSTEM.md", () => {
    expect(readFlatDoc(DOC_PATH)).not.toContain("ChatTabButton.tsx");
  });

  it("no longer names the retired ChatTabButton.tsx in UX_AUTHORING_RULES.md", () => {
    const rules = readFlatDoc(UX_RULES_PATH);

    expect(rules).not.toContain("ChatTabButton.tsx");
    expect(rules).toContain("components/UnderlineTab.tsx");
  });

  it.each(CHARTER_DIMENSIONS)(
    "dispositions the %s charter dimension in DESIGN_SYSTEM.md",
    (label) => {
      const doc = readFileSync(DOC_PATH, "utf-8");

      expect(doc).toContain(label);
    },
  );
});
