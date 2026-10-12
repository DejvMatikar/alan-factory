import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { checkTypes, dumpSchema, generateTypes, main } from "./api-types.mjs";

const SCRIPT_PATH = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "api-types.mjs");

const BASE_SCHEMA = {
  openapi: "3.1.0",
  info: { title: "fixture", version: "1" },
  paths: {
    "/api/things": {
      get: {
        operationId: "listThings",
        responses: {
          "200": {
            description: "ok",
            content: {
              "application/json": { schema: { $ref: "#/components/schemas/Thing" } },
            },
          },
        },
      },
    },
  },
  components: {
    schemas: {
      Thing: {
        type: "object",
        required: ["id"],
        properties: { id: { type: "string" } },
      },
    },
  },
};

function withAddedField(schema) {
  const mutated = structuredClone(schema);
  mutated.components.schemas.Thing.properties.added = { type: "integer" };
  return mutated;
}

const BASE_TEXT = JSON.stringify(BASE_SCHEMA);

async function committedFor(schema) {
  return generateTypes(JSON.stringify(schema));
}

function deps({ dump = () => BASE_TEXT, committed, tracked = true }) {
  return {
    dump,
    readCommitted: () => committed,
    isTracked: () => tracked,
  };
}

describe("generateTypes", () => {
  it("emits the paths and components interfaces for a schema", async () => {
    const generated = await generateTypes(BASE_TEXT);
    expect(generated).toContain("export interface paths");
    expect(generated).toContain("export interface components");
    expect(generated).toContain("Thing");
  });

  it("is deterministic for the same schema", async () => {
    expect(await generateTypes(BASE_TEXT)).toBe(await generateTypes(BASE_TEXT));
  });

  it("changes its output when a model gains a field", async () => {
    const before = await generateTypes(BASE_TEXT);
    const after = await generateTypes(JSON.stringify(withAddedField(BASE_SCHEMA)));
    expect(after).not.toBe(before);
    expect(after).toContain("added");
  });

  it.each([
    ["an empty dump", ""],
    ["a whitespace-only dump", "  \n"],
  ])("rejects %s", async (_label, text) => {
    await expect(generateTypes(text)).rejects.toThrow("empty");
  });

  it("rejects a dump that declares no paths", async () => {
    const noPaths = JSON.stringify({ ...BASE_SCHEMA, paths: {} });
    await expect(generateTypes(noPaths)).rejects.toThrow("no paths");
  });

  it("rejects a dump with no paths key at all", async () => {
    const { paths: _paths, ...withoutPaths } = BASE_SCHEMA;
    await expect(generateTypes(JSON.stringify(withoutPaths))).rejects.toThrow("no paths");
  });

  it("rejects a dump that is not JSON", async () => {
    await expect(generateTypes("not json")).rejects.toThrow();
  });
});

describe("checkTypes", () => {
  it("passes when the committed file equals a fresh generation", async () => {
    const committed = await committedFor(BASE_SCHEMA);
    const result = await checkTypes(deps({ committed }));
    expect(result.ok).toBe(true);
  });

  it("fails when the freshly dumped schema diverges from the committed file", async () => {
    const committed = await committedFor(BASE_SCHEMA);
    const divergent = JSON.stringify(withAddedField(BASE_SCHEMA));
    const result = await checkTypes(deps({ dump: () => divergent, committed }));
    expect(result.ok).toBe(false);
    expect(result.message).toContain("stale");
    expect(result.message).toContain("npm run gen:api");
  });

  it("fails when the committed file was hand-edited away from the generation", async () => {
    const committed = `${await committedFor(BASE_SCHEMA)}export type Handwritten = string;\n`;
    const result = await checkTypes(deps({ committed }));
    expect(result.ok).toBe(false);
  });

  it("fails when the generated file is not tracked even if its content matches", async () => {
    const committed = await committedFor(BASE_SCHEMA);
    const result = await checkTypes(deps({ committed, tracked: false }));
    expect(result.ok).toBe(false);
    expect(result.message).toContain("not tracked");
  });

  it("rejects when the dump itself fails", async () => {
    const committed = await committedFor(BASE_SCHEMA);
    const dump = () => {
      throw new Error("dump exploded");
    };
    await expect(checkTypes(deps({ dump, committed }))).rejects.toThrow("dump exploded");
  });

  it("rejects an empty dump instead of comparing against whatever is on disk", async () => {
    const committed = await committedFor(BASE_SCHEMA);
    await expect(checkTypes(deps({ dump: () => "", committed }))).rejects.toThrow("empty");
  });

  it("rejects when the committed file cannot be read", async () => {
    const readCommitted = () => {
      throw new Error("ENOENT");
    };
    await expect(
      checkTypes({ dump: () => BASE_TEXT, readCommitted, isTracked: () => true }),
    ).rejects.toThrow("ENOENT");
  });
});

describe("dumpSchema", () => {
  function tmpRoot() {
    return fs.mkdtempSync(path.join(os.tmpdir(), "api-types-test-"));
  }

  function writingSpawn(content) {
    return vi.fn((_cmd, args) => {
      fs.writeFileSync(args[args.indexOf("--out") + 1], content);
      return { status: 0 };
    });
  }

  it("returns what the dump script wrote and removes its temp directory", () => {
    const root = tmpRoot();
    const spawn = writingSpawn(BASE_TEXT);
    expect(dumpSchema({ spawn, tmpRoot: root })).toBe(BASE_TEXT);
    expect(fs.readdirSync(root)).toEqual([]);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("dumps to a temp path through scripts/dump_openapi.py, never a committed file", () => {
    const root = tmpRoot();
    const spawn = writingSpawn(BASE_TEXT);
    dumpSchema({ spawn, tmpRoot: root });
    const [command, args] = spawn.mock.calls[0];
    expect(command).toBe("uv");
    expect(args).toContain("scripts/dump_openapi.py");
    expect(args[args.indexOf("--out") + 1].startsWith(root)).toBe(true);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("throws on a non-zero exit from the dump script and still cleans up", () => {
    const root = tmpRoot();
    const spawn = vi.fn(() => ({ status: 1 }));
    expect(() => dumpSchema({ spawn, tmpRoot: root })).toThrow("exit 1");
    expect(fs.readdirSync(root)).toEqual([]);
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("throws when the dump command cannot be spawned", () => {
    const root = tmpRoot();
    const spawn = vi.fn(() => ({ status: null, error: new Error("spawn uv ENOENT") }));
    expect(() => dumpSchema({ spawn, tmpRoot: root })).toThrow("spawn uv ENOENT");
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("throws when the dump script exits zero but wrote nothing", () => {
    const root = tmpRoot();
    const spawn = vi.fn(() => ({ status: 0 }));
    expect(() => dumpSchema({ spawn, tmpRoot: root })).toThrow();
    fs.rmSync(root, { recursive: true, force: true });
  });
});

describe("main", () => {
  it("returns a usage error for an unknown subcommand", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(await main(["bogus"])).toBe(2);
    expect(error).toHaveBeenCalledWith(expect.stringContaining("usage"));
  });

  it.each(["constructor", "toString", "__proto__"])(
    "returns a usage error for the inherited object key %s",
    async (key) => {
      const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
      expect(await main([key])).toBe(2);
      expect(error).toHaveBeenCalledWith(expect.stringContaining("usage"));
    },
  );

  it("check exits 0 and reports the match when the committed file is fresh", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const committed = await committedFor(BASE_SCHEMA);
    expect(await main(["check"], deps({ committed }))).toBe(0);
    expect(log).toHaveBeenCalledWith(expect.stringContaining("matches a fresh generation"));
  });

  it("check exits 1 and reports staleness when the schema diverged", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const committed = await committedFor(BASE_SCHEMA);
    const divergent = JSON.stringify(withAddedField(BASE_SCHEMA));
    expect(await main(["check"], deps({ dump: () => divergent, committed }))).toBe(1);
    expect(error).toHaveBeenCalledWith(expect.stringContaining("stale"));
  });

  it("check exits 1 and prints the reason when the dump fails", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const dump = () => {
      throw new Error("dump exploded");
    };
    expect(await main(["check"], deps({ dump, committed: "" }))).toBe(1);
    expect(error).toHaveBeenCalledWith("dump exploded");
  });

  it("exits 2 when the script is run as a CLI with an unknown subcommand", () => {
    const result = spawnSync("node", [SCRIPT_PATH, "bogus"], { encoding: "utf8" });
    expect(result.status).toBe(2);
    expect(result.stderr).toContain("usage");
  });
});
