import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { cruise, type IViolation } from "dependency-cruiser";
import extractDepcruiseConfig from "dependency-cruiser/config-utl/extract-depcruise-config";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const CONFIG_PATH = path.resolve(process.cwd(), ".dependency-cruiser.cjs");

const PLANTED_FILES: Record<string, string> = {
  "src/features/beta/target.ts": "export const v = 1;\n",
  "src/features/alpha/prod.ts": 'import { v } from "../beta/target";\nexport const w = v;\n',
  "src/features/alpha/prod.test.ts": 'import { v } from "../beta/target";\nexport const w = v;\n',
  "src/shared/planted.ts": 'import { v } from "../features/beta/target";\nexport const w = v;\n',
};

let violations: IViolation[] = [];
let root = "";

function plantFiles(rootDir: string) {
  for (const [relativePath, contents] of Object.entries(PLANTED_FILES)) {
    const filePath = path.join(rootDir, relativePath);
    mkdirSync(path.dirname(filePath), { recursive: true });
    writeFileSync(filePath, contents);
  }
}

beforeAll(async () => {
  root = realpathSync(mkdtempSync(path.join(os.tmpdir(), "feature-rules-")));
  plantFiles(root);

  const config = await extractDepcruiseConfig(CONFIG_PATH);
  const { forbidden } = config;
  if (forbidden === undefined) {
    throw new Error("dependency-cruiser config has no forbidden rules");
  }

  const result = await cruise(["src"], {
    validate: true,
    ruleSet: { forbidden },
    baseDir: root,
  });
  if (typeof result.output === "string") {
    throw new Error("cruise returned a formatted string instead of a result object");
  }
  violations = result.output.summary.violations;
}, 60_000);

afterAll(() => {
  rmSync(root, { recursive: true, force: true });
});

function violationsFrom(importer: string) {
  return violations
    .filter((violation) => violation.from === importer)
    .map((violation) => ({ name: violation.rule.name, severity: violation.rule.severity }));
}

describe("feature boundary rules fire on a planted violation tree", () => {
  it("reports a production file importing another feature under feature-cross", () => {
    expect(violationsFrom("src/features/alpha/prod.ts")).toEqual([
      { name: "feature-cross", severity: "error" },
    ]);
  });

  it("reports a test file importing another feature under feature-cross", () => {
    expect(violationsFrom("src/features/alpha/prod.test.ts")).toEqual([
      { name: "feature-cross", severity: "error" },
    ]);
  });

  it("reports a non-shell production file importing a feature under feature-inbound", () => {
    expect(violationsFrom("src/shared/planted.ts")).toEqual([
      { name: "feature-inbound", severity: "error" },
    ]);
  });
});
