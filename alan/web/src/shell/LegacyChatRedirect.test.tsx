import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes, useLocation, useParams, useSearchParams } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { useInvalidateEnginesList } from "../api/queries";
import { useEngines } from "../hooks/useEngines";
import { REPO_SCOPE_STORAGE_KEY } from "../hooks/useRepoScope";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../test/engineFixtures";
import { server } from "../test/server";
import LegacyChatRedirect, {
  buildScopedChatPath,
  resolveLegacyTargetEngineKey,
  scopedChatSuffix,
  useChatEngineSelfCorrect,
} from "./LegacyChatRedirect";

const ENTRIES = [
  { key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: "autonomous" as const },
  { key: "acme/widget", title: "Widget", posture: "autonomous" as const },
];

beforeEach(() => {
  sessionStorage.clear();
  visited.length = 0;
});

function resolveAgainstEntries(engineParam: string | null, mirroredScope: string | null): string | null {
  return resolveLegacyTargetEngineKey(engineParam, mirroredScope, ENTRIES);
}

describe("resolveLegacyTargetEngineKey", () => {
  it("prefers a valid ?engine= param", () => {
    expect(resolveAgainstEntries("acme/widget", null)).toBe("acme/widget");
  });

  it("falls back to a valid mirrored scope when the param is absent or unknown", () => {
    expect(resolveAgainstEntries(null, "acme/widget")).toBe("acme/widget");
    expect(resolveAgainstEntries("not-registered/x", "acme/widget")).toBe("acme/widget");
  });

  it("falls back to the first entry when neither resolves", () => {
    expect(resolveAgainstEntries(null, null)).toBe(STUB_DEFAULT_ENGINE_KEY);
    expect(resolveAgainstEntries("bogus", "also-bogus")).toBe(STUB_DEFAULT_ENGINE_KEY);
  });

  it("resolves to no repository when neither resolves and there are no entries", () => {
    expect(resolveLegacyTargetEngineKey(null, null, [])).toBeNull();
    expect(resolveLegacyTargetEngineKey("bogus", "also-bogus", [])).toBeNull();
  });
});

describe("scopedChatSuffix", () => {
  it("returns 'build' for the build kind", () => {
    expect(scopedChatSuffix("build", undefined)).toBe("build");
  });

  it("returns explore/:consultant for the explore kind", () => {
    expect(scopedChatSuffix("explore", "architecture")).toBe("explore/architecture");
  });

  it("encodes the consultant key", () => {
    expect(scopedChatSuffix("explore", "a b")).toBe("explore/a%20b");
  });

  it("falls back to an empty consultant segment when none is given", () => {
    expect(scopedChatSuffix("explore", undefined)).toBe("explore/");
  });
});

describe("buildScopedChatPath", () => {
  it("drops the ?engine= param but keeps other params", () => {
    const params = new URLSearchParams({ engine: "acme/widget", s: "sid-1" });
    expect(buildScopedChatPath("acme/widget", "build", undefined, params)).toBe(
      "/repos/acme/widget/build?s=sid-1",
    );
  });

  it("omits the query string entirely when nothing remains", () => {
    const params = new URLSearchParams({ engine: "acme/widget" });
    expect(buildScopedChatPath("acme/widget", "explore", "research", params)).toBe(
      "/repos/acme/widget/explore/research",
    );
  });
});

function mockEngines(): void {
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: [{ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" }],
      }),
    ),
  );
}

function LandedProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="landed">{`${pathname}${search}`}</div>;
}

const visited: string[] = [];

function VisitedProbe() {
  const { pathname, search } = useLocation();
  useEffect(() => {
    visited.push(`${pathname}${search}`);
  }, [pathname, search]);
  return null;
}

function renderLegacy(kind: "build" | "explore", initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <VisitedProbe />
      <Routes>
        <Route path="/" element={<LandedProbe />} />
        <Route path="/build" element={<LegacyChatRedirect kind={kind} />} />
        <Route path="/explore/:consultant" element={<LegacyChatRedirect kind={kind} />} />
        <Route path="/repos/:owner/:repo/build" element={<LandedProbe />} />
        <Route path="/repos/:owner/:repo/explore/:consultant" element={<LandedProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("LegacyChatRedirect", () => {
  it("renders nothing until the engine list has loaded, then redirects using the real list", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/engines", async () => {
        await new Promise<void>((resolve) => {
          gate.resolve = resolve;
        });
        return HttpResponse.json({
          engines: [{ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" }],
        });
      }),
    );
    renderLegacy("build", "/build?engine=acme%2Fwidget");
    expect(screen.queryByTestId("landed")).not.toBeInTheDocument();
    await waitFor(() => expect(gate.resolve).not.toBeNull());
    gate.resolve?.();
    expect(await screen.findByTestId("landed")).toHaveTextContent("/repos/acme/widget/build");
  });

  it("redirects /build with a valid ?engine= to the nested scoped route", async () => {
    mockEngines();
    renderLegacy("build", "/build?engine=acme%2Fwidget");
    expect(await screen.findByTestId("landed")).toHaveTextContent("/repos/acme/widget/build");
  });

  it("falls back to the mirrored session scope when no ?engine= is present", async () => {
    mockEngines();
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/widget");
    renderLegacy("build", "/build");
    expect(await screen.findByTestId("landed")).toHaveTextContent("/repos/acme/widget/build");
  });

  it.each(DEFAULT_KEY_STATES)(
    "falls back to the listed default repository when neither is available (%s)",
    async (_state, defaultKey) => {
      server.use(
        engineListingHandler({ engines: [makeEngineSummaryFor("acme/widget")], defaultKey }),
      );
      renderLegacy("build", "/build");
      expect(await screen.findByTestId("landed")).toHaveTextContent(
        `/repos/${defaultKey}/build`,
      );
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "keeps a valid ?engine= over the listed default repository (%s)",
    async (_state, defaultKey) => {
      server.use(
        engineListingHandler({ engines: [makeEngineSummaryFor("acme/widget")], defaultKey }),
      );
      renderLegacy("build", "/build?engine=acme%2Fwidget");
      expect(await screen.findByTestId("landed")).toHaveTextContent("/repos/acme/widget/build");
    },
  );

  it("falls back to the first registered repository when the list names no default", async () => {
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor("acme/widget")] }));
    renderLegacy("build", "/build");
    expect(await screen.findByTestId("landed")).toHaveTextContent("/repos/acme/widget/build");
  });

  it("sends the user home when nothing is registered and the list names no default", async () => {
    server.use(engineListingHandler({ engines: [] }));
    renderLegacy("build", "/build");
    expect(await screen.findByTestId("landed")).toHaveTextContent(/^\/$/);
  });

  it("ignores a mirrored built-in scope that is not registered under a listed default", async () => {
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor("acme/widget")],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, STUB_DEFAULT_ENGINE_KEY);
    renderLegacy("build", "/build?engine=not-registered%2Fx");
    expect(await screen.findByTestId("landed")).toHaveTextContent(
      `/repos/${LISTED_DEFAULT_KEY}/build`,
    );
  });

  it("renders null and never navigates while the engine list has not loaded", async () => {
    let requested = false;
    server.use(
      http.get("/api/engines", () => {
        requested = true;
        return new Promise(() => undefined);
      }),
    );
    const { container } = renderLegacy("build", "/build");
    await waitFor(() => expect(requested).toBe(true));
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("landed")).not.toBeInTheDocument();
    expect(visited).toEqual(["/build"]);
  });

  it("navigates once, straight to the listed default, after a delayed listing arrives", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/engines", async () => {
        await new Promise<void>((resolve) => {
          gate.resolve = resolve;
        });
        return HttpResponse.json({
          engines: [makeEngineSummaryFor("acme/widget")],
          default_engine_key: LISTED_DEFAULT_KEY,
        });
      }),
    );
    renderLegacy("build", "/build");
    await waitFor(() => expect(gate.resolve).not.toBeNull());
    expect(visited).toEqual(["/build"]);
    gate.resolve?.();
    expect(await screen.findByTestId("landed")).toHaveTextContent(
      `/repos/${LISTED_DEFAULT_KEY}/build`,
    );
    expect(visited).toEqual(["/build", `/repos/${LISTED_DEFAULT_KEY}/build`]);
  });

  it("redirects /explore/:consultant preserving the consultant segment and a resumed ?s=", async () => {
    mockEngines();
    renderLegacy("explore", "/explore/research?engine=acme%2Fwidget&s=sid-9");
    expect(await screen.findByTestId("landed")).toHaveTextContent(
      "/repos/acme/widget/explore/research?s=sid-9",
    );
  });
});

function SelfCorrectProbe({ chatEngineKey }: Readonly<{ chatEngineKey: string | null }>) {
  useChatEngineSelfCorrect("build", chatEngineKey);
  const { owner, repo } = useParams<{ owner: string; repo: string }>();
  const [searchParams] = useSearchParams();
  const qs = searchParams.toString();
  return <div data-testid="probe">{`${owner}/${repo}${qs ? `?${qs}` : ""}`}</div>;
}

describe("useChatEngineSelfCorrect", () => {
  it("redirects to the session's real repository when it differs from the URL scope", async () => {
    render(
      <MemoryRouter initialEntries={["/repos/acme/widget/build?s=sid-1"]}>
        <Routes>
          <Route
            path="/repos/:owner/:repo/build"
            element={<SelfCorrectProbe chatEngineKey="acme/other" />}
          />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId("probe");
    await waitFor(() =>
      expect(screen.getByTestId("probe")).toHaveTextContent("acme/other?s=sid-1"),
    );
  });

  it("does nothing while the session's engine_key is not yet known", () => {
    render(
      <MemoryRouter initialEntries={["/repos/acme/widget/build"]}>
        <Routes>
          <Route
            path="/repos/:owner/:repo/build"
            element={<SelfCorrectProbe chatEngineKey={null} />}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("acme/widget");
  });

  it("does nothing when the session's engine_key already matches the URL scope", () => {
    render(
      <MemoryRouter initialEntries={["/repos/acme/widget/build"]}>
        <Routes>
          <Route
            path="/repos/:owner/:repo/build"
            element={<SelfCorrectProbe chatEngineKey="acme/widget" />}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("acme/widget");
  });
});

function EngineAwareSelfCorrectProbe({ chatEngineKey }: Readonly<{ chatEngineKey: string | null }>) {
  useChatEngineSelfCorrect("build", chatEngineKey);
  const { defaultEngineKey, loaded } = useEngines();
  const invalidate = useInvalidateEnginesList();
  const { pathname, search } = useLocation();
  return (
    <div>
      <div data-testid="resolved-default">{loaded ? defaultEngineKey : ""}</div>
      <div data-testid="scoped-location">{`${pathname}${search}`}</div>
      <button type="button" onClick={invalidate}>
        refresh engines
      </button>
    </div>
  );
}

function renderEngineAwareSelfCorrect(initial: string, chatEngineKey: string | null) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route
          path="/repos/:owner/:repo/build"
          element={<EngineAwareSelfCorrectProbe chatEngineKey={chatEngineKey} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("useChatEngineSelfCorrect under a changing default repository", () => {
  const SCOPED_URL = "/repos/acme/widget/build?s=sid-1";

  it.each(DEFAULT_KEY_STATES)(
    "leaves the URL alone when the listed default arrives after the first render (%s)",
    async (_state, defaultKey) => {
      server.use(
        engineListingHandler({ engines: [makeEngineSummaryFor("acme/widget")], defaultKey }),
      );
      renderEngineAwareSelfCorrect(SCOPED_URL, "acme/widget");
      expect(screen.getByTestId("resolved-default")).toBeEmptyDOMElement();
      await waitFor(() =>
        expect(screen.getByTestId("resolved-default")).toHaveTextContent(
          defaultKey,
        ),
      );
      expect(screen.getByTestId("scoped-location")).toHaveTextContent(SCOPED_URL);
    },
  );

  it("leaves the URL alone when a refetch changes the listed default", async () => {
    const engines = [makeEngineSummaryFor("acme/widget")];
    server.use(engineListingHandler({ engines, defaultKey: STUB_DEFAULT_ENGINE_KEY }));
    renderEngineAwareSelfCorrect(SCOPED_URL, "acme/widget");
    await waitFor(() =>
      expect(screen.getByTestId("resolved-default")).toHaveTextContent(STUB_DEFAULT_ENGINE_KEY),
    );
    server.use(engineListingHandler({ engines, defaultKey: LISTED_DEFAULT_KEY }));
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "refresh engines" }));
    });
    await waitFor(() =>
      expect(screen.getByTestId("resolved-default")).toHaveTextContent(LISTED_DEFAULT_KEY),
    );
    expect(screen.getByTestId("scoped-location")).toHaveTextContent(SCOPED_URL);
  });
});
