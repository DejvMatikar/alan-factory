import type { APIRequestContext, Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { CONTROL_TOKEN_STORAGE_KEY } from "../../src/controlToken";
import { PROJECT_FEATURES_STORAGE_KEY } from "../../src/projectFeatures";

interface MinimalMeta {
  status: string;
  park_reason: string | null;
  updated_at: string;
  [key: string]: unknown;
}

export interface BlockingQuestionOptions {
  status?: string;
  parkReason?: string | null;
  proposedAnswerSpace?: readonly string[];
  recommended?: string | null;
  questionClass?: string | null;
  resolverRole?: string;
  fromTeam?: string;
}

function yamlOrNull(value: string | null): string {
  return value ?? "null";
}

function answerSpaceYaml(space: readonly string[]): string {
  if (space.length === 0) return "    proposed_answer_space: []";
  return ["    proposed_answer_space:", ...space.map((o) => `      - ${o}`)].join("\n");
}

interface ResolvedBlockingQuestionOptions {
  status: string;
  parkReason: string | null;
  proposedAnswerSpace: readonly string[];
  recommended: string | null;
  questionClass: string | null;
  resolverRole: string;
  fromTeam: string;
}

function resolveOptions(opts: BlockingQuestionOptions): ResolvedBlockingQuestionOptions {
  return {
    status: opts.status ?? "awaiting_input",
    parkReason: opts.parkReason === undefined ? "question" : opts.parkReason,
    proposedAnswerSpace: opts.proposedAnswerSpace ?? [],
    recommended: opts.recommended ?? null,
    questionClass: opts.questionClass ?? null,
    resolverRole: opts.resolverRole ?? "human",
    fromTeam: opts.fromTeam ?? "research",
  };
}

function writeMeta(wsPath: string, status: string, parkReason: string | null): void {
  const metaPath = path.join(wsPath, "meta.json");
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as MinimalMeta;
  meta.status = status;
  meta.park_reason = parkReason;
  meta.updated_at = new Date().toISOString();
  fs.writeFileSync(metaPath, JSON.stringify(meta), "utf-8");
}

function writeHandover(
  wsPath: string,
  taskId: string,
  question: string,
  resolved: ResolvedBlockingQuestionOptions,
): void {
  const handoversDir = path.join(wsPath, "handovers");
  fs.mkdirSync(handoversDir, { recursive: true });
  const handoverYaml = [
    'schema_version: "1.11"',
    `handover_id: "handover_${taskId}_001"`,
    "handover_type: forward",
    `task_id: "${taskId}"`,
    `from_team: ${resolved.fromTeam}`,
    "from_invocation_id: inv_000",
    "iteration: 1",
    'timestamp_utc: "2026-06-13T00:00:00+00:00"',
    "full_synthesis: stub synthesis needs a human answer before continuing",
    "open_questions:",
    "  - id: q1",
    `    question: ${JSON.stringify(question)}`,
    answerSpaceYaml(resolved.proposedAnswerSpace),
    `    resolver_role: ${resolved.resolverRole}`,
    "    blocking: true",
    "    prior_attempts: []",
    `    question_class: ${yamlOrNull(resolved.questionClass)}`,
    `    recommended: ${yamlOrNull(resolved.recommended)}`,
  ].join("\n");
  fs.writeFileSync(
    path.join(handoversDir, `handover_${taskId}_001.yaml`),
    handoverYaml,
    "utf-8",
  );
}

export function seedBlockingQuestion(
  e2eRoot: string,
  taskId: string,
  question: string,
  opts: BlockingQuestionOptions = {},
): void {
  const resolved = resolveOptions(opts);
  const wsPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId);
  writeMeta(wsPath, resolved.status, resolved.parkReason);
  writeHandover(wsPath, taskId, question, resolved);
}

export function seedProjectEngineKey(e2eRoot: string, projectId: string, engineKey: string): void {
  const projectPath = path.join(e2eRoot, "projects", projectId, "project.json");
  const project = JSON.parse(fs.readFileSync(projectPath, "utf-8")) as Record<string, unknown>;
  project.engine_key = engineKey;
  fs.writeFileSync(projectPath, JSON.stringify(project), "utf-8");
}

export function seedProjectIntendedEngineKey(
  e2eRoot: string,
  projectId: string,
  intendedEngineKey: string,
): void {
  const projectPath = path.join(e2eRoot, "projects", projectId, "project.json");
  const project = JSON.parse(fs.readFileSync(projectPath, "utf-8")) as Record<string, unknown>;
  project.intended_engine_key = intendedEngineKey;
  fs.writeFileSync(projectPath, JSON.stringify(project), "utf-8");
}

function readControlToken(e2eRoot: string): string {
  return fs.readFileSync(path.join(e2eRoot, "state", "control_token"), "utf-8").trim();
}

export async function registerGuardEngine(
  request: APIRequestContext,
  e2eRoot: string,
  owner: string,
  repo = "widget",
): Promise<void> {
  const res = await request.post("/api/engines", {
    data: { repo: { owner, repo } },
    headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
  });
  if (res.ok() || res.status() === 409) return;
  throw new Error(`failed to register guard engine ${owner}/${repo} (${String(res.status())})`);
}

export async function seedControlToken(page: Page, e2eRoot: string): Promise<void> {
  const token = readControlToken(e2eRoot);
  await page.addInitScript(
    ({ key, value }) => {
      window.localStorage.setItem(key, value);
    },
    { key: CONTROL_TOKEN_STORAGE_KEY, value: token },
  );
}

export async function seedProjectFeatures(page: Page, on: boolean): Promise<void> {
  await page.addInitScript(
    ({ key, value }) => {
      if (value) window.localStorage.setItem(key, "on");
      else window.localStorage.removeItem(key);
    },
    { key: PROJECT_FEATURES_STORAGE_KEY, value: on },
  );
}
