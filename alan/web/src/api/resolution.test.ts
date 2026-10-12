import { existsSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC_DIR = resolve(import.meta.dirname, "..");
const API_DIR = resolve(SRC_DIR, "api");

describe("the bare ./api specifier", () => {
  it("still names the hand-written module file beside the api directory", () => {
    expect(existsSync(resolve(SRC_DIR, "api.ts"))).toBe(true);
    expect(existsSync(API_DIR)).toBe(true);
  });

  it("resolves to src/api.ts rather than src/api/", async () => {
    const resolved = await import("../api");
    expect(resolved.ApiError).toBeTypeOf("function");
    expect(resolved.api).toBeTypeOf("object");
  });

  it("has no index module in the api directory to compete with the file", () => {
    const indexModules = readdirSync(API_DIR).filter((name) => name.startsWith("index."));
    expect(indexModules).toEqual([]);
  });
});
