const FEATURE_INBOUND_EXCEPTIONS = process.env.DEPCRUISE_NO_FEATURE_EXCEPTIONS
  ? []
  : [
      {
        from: "^src/components/ConnectUnboundNotice\\.tsx$",
        to: "^src/features/connect/pages/connectStep4PostureLogic\\.ts$",
      },
    ];

module.exports = {
  forbidden: [
    {
      name: "no-circular",
      severity: "error",
      comment: "Circular dependencies make code harder to test and reason about",
      from: {},
      to: { circular: true },
    },
    {
      name: "no-orphans",
      severity: "warn",
      from: {
        orphan: true,
        pathNot: [
          "\\.d\\.ts$",
          "(^|/)\\.[^/]+\\.(js|cjs|mjs|ts|json)$",
          "(^|/)main\\.tsx?$",
          "(^|/)setup\\.ts$",
          "\\.test\\.tsx?$",
          "\\.spec\\.tsx?$",
        ],
      },
      to: {},
    },
    {
      name: "not-to-test",
      severity: "error",
      comment: "Production code must not import test utilities",
      from: {
        pathNot: ["\\.(test|spec)\\.tsx?$", "(^|/)test/", "(^|/)e2e/"],
      },
      to: {
        path: ["\\.(test|spec)\\.tsx?$", "(^|/)test/", "(^|/)e2e/"],
      },
    },
    {
      name: "query-lib-confined",
      severity: "error",
      comment: "The server-state library is imported only by the query client, its provider, the query module and test support",
      from: {
        pathNot: [
          "^src/api/(queryClient\\.ts|QueryProvider\\.tsx|queries\\.ts)$",
          "^src/test/",
        ],
      },
      to: {
        path: "node_modules/@tanstack/",
        dependencyTypes: ["npm"],
      },
    },
    {
      name: "feature-inbound",
      severity: "error",
      comment: "Only the app shell, tests and the named exceptions import a feature folder",
      from: {
        pathNot: [
          "^src/features/", "^src/App\\.tsx$", "^src/main\\.tsx$",
          "\\.(test|spec)\\.tsx?$", "^src/test/",
          ...FEATURE_INBOUND_EXCEPTIONS.map((e) => e.from),
        ],
      },
      to: { path: "^src/features/" },
    },
    ...FEATURE_INBOUND_EXCEPTIONS.map((e) => ({
      name: "feature-inbound",
      severity: "error",
      comment: "Only the app shell, tests and the named exceptions import a feature folder",
      from: { path: e.from },
      to: { path: "^src/features/", pathNot: e.to },
    })),
    {
      name: "feature-cross",
      severity: "error",
      comment: "A module inside one feature folder may not import from a different feature folder",
      from: { path: "^src/features/([^/]+)/" },
      to: { path: "^src/features/", pathNot: "^src/features/$1/" },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.json" },
    enhancedResolveOptions: {
      exportsFields: ["exports"],
      conditionNames: ["import", "require", "node", "default"],
    },
  },
};
