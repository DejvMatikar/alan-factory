import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";
import tseslint from "typescript-eslint";

const WALL_CLOCK_TIMER_IN_MOCK_MESSAGE =
  "Wall-clock timers inside a mocked network handler race against machine speed instead of " +
  "guarding against it. Use an armed gate the test releases explicitly (see " +
  "src/features/home/hooks/useConnectStates.test.tsx) or a promise that never settles when only the " +
  "in-flight window matters (see src/features/chat/hooks/useProposePrefetch.test.ts).";

const NESTED_TIMER_CALL =
  "CallExpression:matches(" +
  "[callee.name=/^(setTimeout|setInterval|delay)$/], " +
  "[callee.object.name=/^(window|globalThis)$/][callee.property.name=/^(setTimeout|setInterval)$/]" +
  ")";

const WALL_CLOCK_WAIT_IN_E2E_MESSAGE =
  ".waitForTimeout() waits a fixed wall-clock duration instead of a condition, so it either " +
  "flakes red on a slow runner or silently masks the real settle condition on a fast one. Prefer " +
  "a Playwright auto-waiting assertion (expect(locator)...toBeVisible()/toHaveText()/...) or a " +
  "condition-based poll (see waitForInteractiveCountStable in e2e/mobile-audit/mobile-audit.spec.ts). " +
  "New e2e specs may not add .waitForTimeout() calls — see docs/architecture/e2e_testing.md for the " +
  "grandfathered exceptions and their per-file justification.";

const QUERY_CALL_OUTSIDE_QUERIES_MESSAGE =
  "Server-state query and mutation hooks are called only from src/api/queries.ts. Add a named " +
  "hook there and import it, so the client's defaults and query keys stay in one module — see " +
  "docs/architecture/frontend.md's server-state section.";

const QUERY_CALL_OUTSIDE_QUERIES = {
  selector:
    "CallExpression[callee.name=/^(useQuery|useQueries|useInfiniteQuery|useSuspenseQuery|" +
    "useSuspenseQueries|useSuspenseInfiniteQuery|usePrefetchQuery|usePrefetchInfiniteQuery|" +
    "useMutation|useQueryClient|useIsFetching|useIsMutating|useMutationState)$/]",
  message: QUERY_CALL_OUTSIDE_QUERIES_MESSAGE,
};

const PROSE_COMMENT_IN_TEST_MESSAGE =
  "Test code carries no comments and no docstrings (CLAUDE.md). The test's name is its " +
  "documentation: a case that needs explaining needs renaming, not a comment above it. Delete " +
  "this and fold what it says into the it()/describe() title, the fixture's name, or the PR body. " +
  "Only machine-read directives may stay (eslint-disable, @ts-expect-error, /// <reference>); an " +
  "empty catch {} needs no // ignore filler and an empty callback is () => undefined.";

const MACHINE_READ_COMMENT =
  /^\/?\s*(?:eslint-|globals?\s|@ts-|prettier-ignore|istanbul\s+ignore|[vc]8\s+ignore|@vitest-|@jest-|@license|@preserve|<reference)/;

const testCommentPlugin = {
  rules: {
    "no-prose-comments": {
      meta: {
        type: "problem",
        schema: [],
        messages: { prose: PROSE_COMMENT_IN_TEST_MESSAGE },
      },
      create(context) {
        return {
          Program() {
            for (const comment of context.sourceCode.getAllComments()) {
              if (!MACHINE_READ_COMMENT.test(comment.value)) {
                context.report({ loc: comment.loc, messageId: "prose" });
              }
            }
          },
        };
      },
    },
  },
};

export default tseslint.config(
  {
    ignores: [
      "dist/",
      "node_modules/",
      "coverage/",
      "playwright-report/",
      "test-results/",
      "scripts/",
      "src/api/schema.ts",
      ".reversed-shims/",
      ".knip.json",
      "vite.config.ts",
      "vitest.config.ts",
      "vitest.no-isolation.config.ts",
      "playwright.config.ts",
      "playwright.mobile.config.ts",
      "playwright.desktop.config.ts",
      "playwright.capture.config.ts",
      "playwright.reversed.config.ts",
      "eslint.config.js",
      "postcss.config.js",
      ".dependency-cruiser.cjs",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        project: ["./tsconfig.json"],
        tsconfigRootDir: import.meta.dirname,
      },
      globals: { ...globals.browser },
    },
    plugins: {
      "react-hooks": reactHooks,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/no-unused-vars": "error",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      complexity: ["error", 8],
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
  {
    files: [
      "**/*.test.{ts,tsx}",
      "src/test/**",
      "e2e/**",
    ],
    plugins: { "test-comments": testCommentPlugin },
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-non-null-assertion": "off",
      "no-empty": ["error", { allowEmptyCatch: true }],
      "test-comments/no-prose-comments": "error",
    },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/api/queries.ts"],
    rules: {
      "no-restricted-syntax": ["error", QUERY_CALL_OUTSIDE_QUERIES],
    },
  },
  {
    files: ["src/**/*.test.{ts,tsx}", "src/test/**"],
    rules: {
      "no-restricted-syntax": [
        "error",
        QUERY_CALL_OUTSIDE_QUERIES,
        {
          selector:
            `CallExpression[callee.object.name='http'][callee.property.name=/^(get|post|put|patch|delete|all|options|head)$/] ${NESTED_TIMER_CALL}`,
          message: WALL_CLOCK_TIMER_IN_MOCK_MESSAGE,
        },
        {
          selector:
            `CallExpression[callee.property.name=/^mockImplementation(Once)?$/] ${NESTED_TIMER_CALL}`,
          message: WALL_CLOCK_TIMER_IN_MOCK_MESSAGE,
        },
        {
          selector:
            `AssignmentExpression[left.object.property.name='api'] ${NESTED_TIMER_CALL}`,
          message: WALL_CLOCK_TIMER_IN_MOCK_MESSAGE,
        },
      ],
    },
  },
  {
    files: ["e2e/**/*.spec.ts"],
    ignores: [
      "e2e/capture/capture.spec.ts",
      "e2e/mobile-audit/mobile-audit.spec.ts",
      "e2e/project-ops-panel.spec.ts",
    ],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression[callee.property.name='waitForTimeout']",
          message: WALL_CLOCK_WAIT_IN_E2E_MESSAGE,
        },
      ],
    },
  },
);
