import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

type Category = "view-model" | "vocabulary" | "mirror" | "cut-group";

interface Exception {
  category: Category;
  reason: string;
}

const EXCEPTIONS: Readonly<Record<string, Exception>> = {
  ParsedSseEvent: {
    category: "view-model",
    reason: "Shape of one parsed Server-Sent Events frame; produced by the client, never sent by the backend.",
  },
  WorkspaceEventHandlers: { category: "view-model", reason: "Callback contract for subscribeWorkspace." },
  ServerEventsHandlers: { category: "view-model", reason: "Callback contract for subscribeEvents." },
  ChatEventHandlers: {
    category: "view-model",
    reason: "Callback contract shared by the build and explore chat subscriptions.",
  },
  CreateAndDispatchWorkspaceOptions: {
    category: "view-model",
    reason: "Option bag for createAndDispatchWorkspace.",
  },
  CheckCollisionOptions: { category: "view-model", reason: "Option bag for checkCollision." },
  CompleteProjectPayload: {
    category: "view-model",
    reason:
      "Option bag for completeProject. The route's CompleteRequest has no plain schema component " +
      "(the generated name is alan__routes__projects__CompleteRequest), and the wrapper fills the " +
      "force and reason defaults itself before it builds the body.",
  },
  Assumption: {
    category: "view-model",
    reason:
      "Built on the client by deriveAssumptions from the backend's assumptions: list[str], so it " +
      "has no wire shape to alias.",
  },
  EngineConfigFinding: {
    category: "view-model",
    reason: "Built on the client by classifyEngineConfigFinding from a plain message string; no wire shape.",
  },
  NodeDetailTab: {
    category: "view-model",
    reason: "The output, prompt or config tab a node's detail modal shows; a UI selection, never sent or received.",
  },
  StreamEvent: {
    category: "view-model",
    reason:
      "The agent-stream route returns list[dict[str, Any]], so the schema names no component; " +
      "StreamEvent narrows one entry to the type-tagged record the stream panel reads.",
  },
  AssumptionStatus: {
    category: "vocabulary",
    reason: "Status union of the client-built Assumption view model.",
  },
  EngineConfigFindingScope: {
    category: "vocabulary",
    reason: "Scope union of the client-derived EngineConfigFinding view model.",
  },
  ChangeKind: {
    category: "vocabulary",
    reason:
      "Kinds carried by the server-events changed frame, a Server-Sent Events payload the OpenAPI " +
      "export cannot describe.",
  },
  ChatSseEventKind: {
    category: "vocabulary",
    reason:
      "Event names of the chat subscription stream, a Server-Sent Events vocabulary the OpenAPI " +
      "export cannot describe.",
  },
  ClientResult: {
    category: "vocabulary",
    reason:
      "The result envelope unwrap accepts from the generated client, deliberately looser than the " +
      "client's per-operation response type so one helper serves every operation.",
  },
  DispatchProposal: {
    category: "mirror",
    reason:
      "Proposal union member: the normalised ready proposal, whose instructions and scope_audit are " +
      "required once normalizeDispatchProposalResponse has filled them, where the generated " +
      "DispatchProposal marks both optional. Kept hand-written by the PR 7 ruling; only its transport moved.",
  },
  DispatchProposalResponse: {
    category: "mirror",
    reason:
      "Proposal union: both generated arms type status as a plain string with a default rather than a " +
      "literal, so a tagged union stops narrowing, and the ready arm embeds the collision component. " +
      "Kept hand-written by the PR 7 ruling; only its transport moved.",
  },
  CollisionMatchInfo: {
    category: "mirror",
    reason:
      "Collision pair: the match member of CollisionCheckResponse, declared beside its parent, which " +
      "stays hand-written for the reason recorded there.",
  },
  CollisionCheckResponse: {
    category: "mirror",
    reason:
      "Collision pair: one generated component, CollisionVerdictOut, serves both the collision route and " +
      "the ready proposal's collision field, and its match is optional-nullable against required-nullable " +
      "here, so toCollisionCheckResponse widens it with ?? null at the two reads.",
  },
  ProposableEngineConfigField: {
    category: "mirror",
    reason:
      "Configuration declaration kept by design: the hand-picked subset of EngineConfig fields the " +
      "config-proposal pipeline touches, which no schema component names.",
  },
  ProposalFieldDiff: {
    category: "mirror",
    reason:
      "Configuration declaration kept by design: the generated shapes degrade the field discriminant to a " +
      "bare string and the values to unknown, which destroys the narrowing the diff table depends on.",
  },
  ConfigProposalResult: {
    category: "mirror",
    reason:
      "Configuration declaration kept by design: it carries ProposalFieldDiff, so it cannot alias the " +
      "generated result without losing the narrowing.",
  },
  ConfigProposalBasePreset: {
    category: "mirror",
    reason:
      "Preset union that tests/test_frontend_preset_catalogue.py parses out of api.ts to pin against " +
      "alan.engine_presets.PRESETS; deriving it from the generated request would need that backend test " +
      "rewritten first.",
  },
  DispatchPayload: {
    category: "mirror",
    reason:
      "Caller-side option bag of dispatchTask and createAndDispatchWorkspace: buildDispatchBody trims " +
      "instructions and drops a blank one before the wire body is built, so the caller shape is not the wire shape.",
  },
  TaskProposal: {
    category: "mirror",
    reason:
      "The tasks argument of createWorkspace: the wire entry is the generated TaskCreateEntry, which lacks " +
      "the title, description, instructions and scope_audit callers supply, and the body is cast at the request.",
  },
  UpdateProjectPayload: {
    category: "mirror",
    reason:
      "Named request-shape exception from PR 6: PATCH applies exclude_unset, so title, status and autonomy " +
      "must stay non-null where the generated PatchProjectBody adds | null.",
  },
  ControlTokenLifecycleState: {
    category: "cut-group",
    reason:
      "Control-token group, cut from the migration by decision: its calls reach sanitizeApiUrl over a raw " +
      "fetch, one deliberately sending no credential, and its types stay with it.",
  },
  ControlTokenStatusBody: {
    category: "cut-group",
    reason:
      "Control-token group, cut from the migration by decision: its calls reach sanitizeApiUrl over a raw " +
      "fetch, one deliberately sending no credential, and its types stay with it.",
  },
  ControlTokenStatus: {
    category: "cut-group",
    reason:
      "Control-token group, cut from the migration by decision, and it adds serverNowMs read from the " +
      "response's Date header, which the wire body does not carry.",
  },
  DeleteSecretPayload: {
    category: "cut-group",
    reason:
      "The body-bearing DELETE /api/secrets stays a bespoke fetch by decision, answering 204 with no " +
      "content; its payload declaration stays with it.",
  },
};

const PRESET_CATALOGUE_TEST = "tests/test_frontend_preset_catalogue.py";
const COMMENT_OR_STRING_RE = /\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`/g;
const DECLARATION_RE = /^(?:export\s+)?(interface|type)\s+(\w+)([\s\S]*)$/;
const SCHEMA_MARKER = 'components["schemas"]';

interface Declaration {
  kind: string;
  name: string;
  body: string;
}

function withoutComments(source: string): string {
  return source.replace(COMMENT_OR_STRING_RE, (token) => (token.startsWith("/") ? " " : token));
}

function blankStrings(text: string): string {
  return text.replace(COMMENT_OR_STRING_RE, '""');
}

function topLevelDeclarations(source: string): Declaration[] {
  return withoutComments(source)
    .split(/\n(?=[^\s})\]])/)
    .flatMap((chunk) => {
      const match = DECLARATION_RE.exec(chunk);
      return match === null ? [] : [{ kind: match[1]!, name: match[2]!, body: match[3]! }];
    });
}

function isAliasShaped(declaration: Declaration): boolean {
  return declaration.kind === "type" && !declaration.body.includes("{");
}

function mentionsAny(text: string, names: ReadonlySet<string>): boolean {
  return (blankStrings(text).match(/\b\w+\b/g) ?? []).some((word) => names.has(word));
}

function derivedNames(declarations: readonly Declaration[]): Set<string> {
  const derived = new Set<string>();
  const aliases = declarations.filter(isAliasShaped);
  let grew = true;
  while (grew) {
    grew = false;
    for (const alias of aliases) {
      const isDerived = alias.body.includes(SCHEMA_MARKER) || mentionsAny(alias.body, derived);
      if (isDerived && !derived.has(alias.name)) {
        derived.add(alias.name);
        grew = true;
      }
    }
  }
  return derived;
}

function handWrittenNames(source: string): string[] {
  const declarations = topLevelDeclarations(source);
  const derived = derivedNames(declarations);
  return declarations.map((d) => d.name).filter((name) => !derived.has(name)).sort();
}

const API_SOURCE = readFileSync(path.resolve(process.cwd(), "src/api.ts"), "utf-8");
const HAND_WRITTEN = handWrittenNames(API_SOURCE);
const EXCEPTION_NAMES = Object.keys(EXCEPTIONS).sort();

describe("hand-written declaration drift guard", () => {
  it("fails on a hand-written declaration with no recorded reason: an unexplained mirror", () => {
    const unexplained = HAND_WRITTEN.filter((name) => !(name in EXCEPTIONS));
    expect(
      unexplained,
      `api.ts declares ${unexplained.join(", ")} by hand with no recorded reason. Derive it from the ` +
        'generated schema (export type X = components["schemas"]["..."]); only if it genuinely cannot be, ' +
        "add it to EXCEPTIONS with the reason. The list is tightening-only: adding an entry to land a " +
        "mirror is a gate downgrade, not a fix.",
    ).toEqual([]);
  });

  it("fails on an exception entry that is no longer a hand-written declaration: a stale entry", () => {
    const stale = EXCEPTION_NAMES.filter((name) => !HAND_WRITTEN.includes(name));
    expect(
      stale,
      `EXCEPTIONS lists ${stale.join(", ")}, which api.ts no longer declares by hand: it was derived or ` +
        "deleted. Delete the entry; the list may only shrink.",
    ).toEqual([]);
  });

  it("records a substantive reason for every exception", () => {
    for (const [name, { reason }] of Object.entries(EXCEPTIONS)) {
      expect(reason.trim().length, `${name} has no substantive reason`).toBeGreaterThan(20);
    }
  });

  it("keeps exactly eleven permanent frontend-only view models and never lists EngineSummary", () => {
    const viewModels = EXCEPTION_NAMES.filter((name) => EXCEPTIONS[name]?.category === "view-model");
    expect(
      viewModels,
      "the permanent frontend-only view models number eleven; EngineSummary was removed from the table " +
        "because it has a backend counterpart, so twelve is the stale figure.",
    ).toHaveLength(11);
    expect(EXCEPTION_NAMES).not.toContain("EngineSummary");
  });

  it("ties the preset union's reason to the backend test that parses it out of api.ts", () => {
    expect(EXCEPTIONS.ConfigProposalBasePreset?.reason).toContain(PRESET_CATALOGUE_TEST);
    expect(existsSync(path.resolve(process.cwd(), "../..", PRESET_CATALOGUE_TEST))).toBe(true);
  });
});

describe("hand-written declaration detection", () => {
  const SOURCE = [
    'export type Base = components["schemas"]["Base"];',
    "export type Alias = Base | null;",
    "export interface Wraps {",
    "  base: Base;",
    "}",
    "export type Mapped = {",
    "  [K in keyof Base]: Base[K];",
    "}[keyof Base];",
    'type Literals = "a" | "b";',
    'type MentionsInString = "Base" | "b";',
    "interface Mirror {",
    "  a: string;",
    "}",
    '// type Commented = "x";',
  ].join("\n");

  it("treats an interface or object-literal body that merely references a derived name as hand-written", () => {
    expect(handWrittenNames(SOURCE)).toEqual(["Literals", "Mapped", "MentionsInString", "Mirror", "Wraps"]);
  });

  it("derives an alias of a derived alias transitively", () => {
    expect(handWrittenNames(SOURCE)).not.toContain("Alias");
  });
});
