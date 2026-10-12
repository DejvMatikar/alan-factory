import { ESLint, Linter } from "eslint";
import tseslint from "typescript-eslint";
import { describe, expect, it } from "vitest";

const cachedConfigForFile = new Map<string, Promise<Linter.Config>>();

function configForFile(filename: string): Promise<Linter.Config> {
  let cached = cachedConfigForFile.get(filename);
  if (cached === undefined) {
    cached = (async () => {
      const eslint = new ESLint({ cwd: process.cwd() });
      return (await eslint.calculateConfigForFile(filename)) as Linter.Config;
    })();
    cachedConfigForFile.set(filename, cached);
  }
  return cached;
}

async function configuredRuleForFile(filename: string): Promise<Linter.RuleEntry | undefined> {
  const config = await configForFile(filename);
  return config.rules?.["no-restricted-syntax"];
}

async function ruleForFile(filename: string): Promise<Linter.RuleEntry> {
  const rule = await configuredRuleForFile(filename);
  if (rule === undefined) throw new Error(`no-restricted-syntax is not configured for ${filename}`);
  return rule;
}

function noRestrictedSyntaxRule(): Promise<Linter.RuleEntry> {
  return ruleForFile("src/test/lintRules.test.ts");
}

function lintSnippet(code: string, rule: Linter.RuleEntry) {
  const linter = new Linter();
  const config: Linter.Config[] = [
    {
      files: ["**/*.ts", "**/*.tsx"],
      languageOptions: {
        parser: tseslint.parser,
        ecmaVersion: 2022,
        sourceType: "module",
      },
      rules: { "no-restricted-syntax": rule },
    },
  ];
  return linter.verify(code, config, { filename: "snippet.ts" });
}

describe("no-restricted-syntax: wall-clock timers in mocked network handlers", () => {
  it("flags a setTimeout inside an http.post handler body, citing both safe alternatives", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        http.post(PATH, async () => {
          await new Promise((resolve) => setTimeout(resolve, 200));
          return HttpResponse.json({});
        });
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
    expect(messages[0]!.message).toContain("useConnectStates.test.tsx");
    expect(messages[0]!.message).toContain("useProposePrefetch.test.ts");
  });

  it("reports exactly once (not twice) when the handler is wrapped in server.use(...)", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        server.use(
          http.post(PATH, async () => {
            await new Promise((resolve) => setTimeout(resolve, 200));
            return HttpResponse.json({});
          }),
        );
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
  });

  it("flags a setInterval inside a mockImplementationOnce body", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        vi.fn().mockImplementationOnce(() => {
          setInterval(() => {}, 100);
        });
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
  });

  it("flags a setTimeout inside a direct api-module reassignment", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        apiMod.api.getDelivery = () => {
          setTimeout(() => {}, 50);
          return Promise.resolve();
        };
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
  });

  it("does not flag a bare test-body setTimeout", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        setTimeout(() => {}, 100);
      `,
      rule,
    );
    expect(messages).toHaveLength(0);
  });

  it("does not flag the converted armed-gate shape", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        http.post(PATH, () => new Promise((resolve) => { gate.settle = resolve; }));
      `,
      rule,
    );
    expect(messages).toHaveLength(0);
  });

  it("flags an msw delay() call inside an http.get handler body, citing both safe alternatives", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        http.get(PATH, async () => {
          await delay(120);
          return HttpResponse.json({});
        });
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
    expect(messages[0]!.message).toContain("useConnectStates.test.tsx");
    expect(messages[0]!.message).toContain("useProposePrefetch.test.ts");
  });

  it("flags an msw delay() call inside a mockImplementationOnce body", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        vi.fn().mockImplementationOnce(async () => {
          await delay(120);
        });
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
  });

  it("flags an msw delay() call inside a direct api-module reassignment", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        apiMod.api.getDelivery = async () => {
          await delay(50);
          return Promise.resolve();
        };
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
  });

  it("does not flag a bare test-body delay() call", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        delay(20);
      `,
      rule,
    );
    expect(messages).toHaveLength(0);
  });

  it("flags a qualified window.setTimeout call inside an http handler body", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        http.get(PATH, async () => {
          await new Promise((resolve) => window.setTimeout(resolve, 200));
          return HttpResponse.json({});
        });
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
  });

  it("flags a qualified globalThis.setInterval call inside a mockImplementationOnce body", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        vi.fn().mockImplementationOnce(() => {
          globalThis.setInterval(() => {}, 100);
        });
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
  });
});

describe("no-restricted-syntax: server-state hooks outside src/api/queries.ts", () => {
  it.each(["useQuery", "useMutation", "useQueries", "useQueryClient", "useInfiniteQuery"])(
    "flags a %s call in a production component, pointing at the frontend architecture doc",
    async (hook) => {
      const rule = await ruleForFile("src/components/Anything.tsx");
      const messages = lintSnippet(`${hook}({});`, rule);
      expect(messages).toHaveLength(1);
      expect(messages[0]!.message).toContain("src/api/queries.ts");
      expect(messages[0]!.message).toContain("docs/architecture/frontend.md");
    },
  );

  it("flags a useQuery call in a test file", async () => {
    const rule = await noRestrictedSyntaxRule();
    expect(lintSnippet("useQuery({});", rule)).toHaveLength(1);
  });

  it("does not flag a call to a named hook exported by the query module", async () => {
    const rule = await ruleForFile("src/components/Anything.tsx");
    expect(lintSnippet("useHealthQuery();", rule)).toHaveLength(0);
  });

  it("configures no restriction at all for the query module itself", async () => {
    expect(await configuredRuleForFile("src/api/queries.ts")).toBeUndefined();
  });

  it("keeps the three wall-clock-timer selectors alongside the query selector for a test file", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(
      `
        http.get(PATH, async () => {
          await delay(10);
        });
        vi.fn().mockImplementationOnce(() => {
          setTimeout(() => {}, 1);
        });
        apiMod.api.getDelivery = () => {
          setInterval(() => {}, 1);
        };
        useQuery({});
      `,
      rule,
    );
    expect(messages).toHaveLength(4);
  });
});

describe("no-restricted-syntax: wall-clock waits in the browser e2e suite", () => {
  const WAIT_SNIPPET = `
    test("does something", async ({ page }) => {
      await page.waitForTimeout(1000);
    });
  `;

  it("flags page.waitForTimeout() in an e2e spec outside the grandfathered allowlist", async () => {
    const rule = await ruleForFile("e2e/archive-project.spec.ts");
    const messages = lintSnippet(WAIT_SNIPPET, rule);
    expect(messages).toHaveLength(1);
    expect(messages[0]!.message).toContain("docs/architecture/e2e_testing.md");
  });

  it.each([
    "e2e/capture/capture.spec.ts",
    "e2e/mobile-audit/mobile-audit.spec.ts",
    "e2e/project-ops-panel.spec.ts",
  ])("does not configure the wall-clock-wait ban for grandfathered file %s", async (filename) => {
    const rule = await configuredRuleForFile(filename);
    expect(rule).toBeUndefined();
  });

  it("does not flag page.waitForTimeout() in a unit test file (rule is e2e-spec-scoped)", async () => {
    const rule = await noRestrictedSyntaxRule();
    const messages = lintSnippet(WAIT_SNIPPET, rule);
    expect(messages).toHaveLength(0);
  });

  it("flags a non-page object's waitForTimeout() call with an object-name-agnostic message", async () => {
    const rule = await ruleForFile("e2e/archive-project.spec.ts");
    const messages = lintSnippet(
      `
        test("does something", async ({ page }) => {
          const popup = await page.waitForEvent("popup");
          await popup.waitForTimeout(1000);
        });
      `,
      rule,
    );
    expect(messages).toHaveLength(1);
    expect(messages[0]!.message).not.toContain("page.waitForTimeout()");
  });
});
