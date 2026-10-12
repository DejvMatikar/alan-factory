/**
 * Generate src/api/schema.ts from the backend's own OpenAPI export, and check
 * that the committed copy still equals a fresh generation.
 *
 * `check` closes the ways a drift gate falsely passes: it dumps the schema
 * from the running application definition into a temp path on every run (no
 * committed schema JSON to regenerate from), compares file CONTENT rather than
 * `git diff` output (an untracked or ignored file has no diff), requires the
 * generated file to be tracked, and treats a failed or empty dump as a hard
 * failure. Both subcommands share generateTypes, so what `generate` writes is
 * exactly what `check` compares against.
 *
 * Usage (from alan/web): node scripts/api-types.mjs generate | check
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";

const WEB_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(WEB_ROOT, "..", "..");

export const GENERATED_PATH = "src/api/schema.ts";

export async function generateTypes(schemaText) {
  if (schemaText.trim() === "") {
    throw new Error("OpenAPI dump is empty");
  }
  const schema = JSON.parse(schemaText);
  if (Object.keys(schema.paths ?? {}).length === 0) {
    throw new Error("OpenAPI dump declares no paths");
  }
  const ast = await openapiTS(schema, { silent: true });
  return `${COMMENT_HEADER}${astToString(ast)}`;
}

export async function checkTypes({ dump, readCommitted, isTracked }) {
  const fresh = await generateTypes(dump());
  if (!isTracked()) {
    return {
      ok: false,
      message: `${GENERATED_PATH} is not tracked by git; a diff cannot see an untracked file`,
    };
  }
  if (readCommitted() !== fresh) {
    return {
      ok: false,
      message: `${GENERATED_PATH} is stale against the backend schema; run \`npm run gen:api\` and commit the result`,
    };
  }
  return { ok: true, message: `${GENERATED_PATH} matches a fresh generation` };
}

export function dumpSchema({ spawn = spawnSync, tmpRoot = os.tmpdir() } = {}) {
  const dir = fs.mkdtempSync(path.join(tmpRoot, "alan-openapi-"));
  const out = path.join(dir, "openapi.json");
  try {
    const result = spawn(
      "uv",
      ["run", "python", "scripts/dump_openapi.py", "--out", out],
      { cwd: REPO_ROOT, stdio: ["ignore", "inherit", "inherit"] },
    );
    if (result.error || result.status !== 0) {
      throw new Error(
        `scripts/dump_openapi.py failed (${result.error?.message ?? `exit ${result.status}`})`,
      );
    }
    return fs.readFileSync(out, "utf8");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function isTrackedByGit() {
  return (
    spawnSync("git", ["ls-files", "--error-unmatch", "--", GENERATED_PATH], {
      cwd: WEB_ROOT,
      stdio: "ignore",
    }).status === 0
  );
}

function readCommittedFile() {
  return fs.readFileSync(path.join(WEB_ROOT, GENERATED_PATH), "utf8");
}

async function generate() {
  const target = path.join(WEB_ROOT, GENERATED_PATH);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, await generateTypes(dumpSchema()), "utf8");
  console.log(`wrote ${GENERATED_PATH}`);
  return 0;
}

async function check({
  dump = dumpSchema,
  readCommitted = readCommittedFile,
  isTracked = isTrackedByGit,
} = {}) {
  const { ok, message } = await checkTypes({ dump, readCommitted, isTracked });
  (ok ? console.log : console.error)(message);
  return ok ? 0 : 1;
}

export async function main(argv, seams = {}) {
  const commands = new Map([
    ["generate", generate],
    ["check", check],
  ]);
  const command = commands.get(argv[0]);
  if (!command) {
    console.error("usage: node scripts/api-types.mjs generate | check");
    return 2;
  }
  try {
    return await command(seams);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(await main(process.argv.slice(2)));
}
