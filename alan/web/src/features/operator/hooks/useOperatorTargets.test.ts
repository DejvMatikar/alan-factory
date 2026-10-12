import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { WorkspaceSummary } from "../../../api";
import type { OperatorScope } from "../../../api/operator";
import { queryClient } from "../../../api/queryClient";
import { deferredFetch } from "../../../test/docReadHarness";
import {
  engineListingHandler,
  gatedEngineListingHandler,
  makeEngineSummaryFor,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import { useOperatorTargets } from "./useOperatorTargets";

const DEFAULT_KEY = "DejvMatikar/alan";

function serveRepos(keys: readonly string[], hidden: readonly string[] = []) {
  server.use(
    engineListingHandler({
      engines: [
        ...keys.map((key) => makeEngineSummaryFor(key)),
        ...hidden.map((key) => makeEngineSummaryFor(key, { hidden: true })),
      ],
      defaultKey: DEFAULT_KEY,
    }),
  );
}

function serveWorkspaces(rows: readonly WorkspaceSummary[]) {
  const seen = { calls: 0, includes: [] as (string | null)[] };
  server.use(
    http.get("/api/workspaces", ({ request }) => {
      seen.calls += 1;
      seen.includes.push(new URL(request.url).searchParams.get("include"));
      return HttpResponse.json(rows);
    }),
  );
  return seen;
}

function stuck(taskId: string, overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return makeWorkspaceSummary({
    task_id: taskId,
    title: `Title of ${taskId}`,
    status: "escalated",
    operator_link: true,
    engine_key: "acme/widget",
    ...overrides,
  });
}

function renderTargets(scope: OperatorScope, preselected = "") {
  return renderHook(
    (props: { scope: OperatorScope; preselected: string }) =>
      useOperatorTargets(props.scope, props.preselected),
    { initialProps: { scope, preselected } },
  );
}

type Rendered = ReturnType<typeof renderTargets>;

async function settled(result: Rendered["result"]): Promise<void> {
  await waitFor(() => {
    expect(result.current.status).not.toBe("loading");
  });
}

function labels(result: Rendered["result"]): string[] {
  return result.current.options.map((option) => option.label);
}

describe("useOperatorTargets — the global scope", () => {
  it("needs no target, offers no options and does not ask for the workspace list", async () => {
    serveRepos(["acme/widget"]);
    const seen = serveWorkspaces([stuck("fix-it")]);
    const { result } = renderTargets("fleet");
    await waitFor(() => {
      expect(queryClient.isFetching()).toBe(0);
    });
    expect(result.current).toEqual({ status: "ready", options: [], initial: "" });
    expect(seen.calls).toBe(0);
  });
});

describe("useOperatorTargets — the repository scope", () => {
  it("lists each visible connected repository once, by slug, with the default first", async () => {
    serveRepos(["acme/zeta", DEFAULT_KEY, "acme/alpha", "acme/alpha"], ["acme/secret"]);
    const { result } = renderTargets("repo");
    await settled(result);
    expect(result.current.status).toBe("ready");
    expect(labels(result)).toEqual([DEFAULT_KEY, "acme/alpha", "acme/zeta"]);
    expect(result.current.options.map((option) => option.value)).toEqual([
      DEFAULT_KEY,
      "acme/alpha",
      "acme/zeta",
    ]);
  });

  it("does not offer a hidden repository or the default when the list omits it", async () => {
    serveRepos(["acme/alpha"], [DEFAULT_KEY]);
    const { result } = renderTargets("repo");
    await settled(result);
    expect(labels(result)).toEqual(["acme/alpha"]);
  });

  it("preselects the only repository", async () => {
    serveRepos(["acme/widget"]);
    const { result } = renderTargets("repo");
    await settled(result);
    expect(result.current.initial).toBe("acme/widget");
  });

  it("leaves a choice of several repositories unselected", async () => {
    serveRepos(["acme/widget", "acme/gadget"]);
    const { result } = renderTargets("repo");
    await settled(result);
    expect(result.current.initial).toBe("");
  });

  it("preselects a named repository that is connected", async () => {
    serveRepos(["acme/widget", "acme/gadget"]);
    const { result } = renderTargets("repo", "acme/widget");
    await settled(result);
    expect(result.current.initial).toBe("acme/widget");
  });

  it("ignores a named repository that is not connected", async () => {
    serveRepos(["acme/widget", "acme/gadget"]);
    const { result } = renderTargets("repo", "acme/gone");
    await settled(result);
    expect(result.current.initial).toBe("");
  });

  it("still preselects the only repository when the named one is not connected", async () => {
    serveRepos(["acme/widget"]);
    const { result } = renderTargets("repo", "acme/gone");
    await settled(result);
    expect(result.current.initial).toBe("acme/widget");
  });

  it("is loading until the repository list answers", async () => {
    const gated = gatedEngineListingHandler({ engines: [makeEngineSummaryFor("acme/widget")] });
    server.use(gated.handler);
    const { result } = renderTargets("repo");
    expect(result.current.status).toBe("loading");
    gated.release();
    await settled(result);
    expect(result.current.status).toBe("ready");
  });

  it("fails when the repository list cannot be read", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.error()));
    const { result } = renderTargets("repo");
    await waitFor(() => {
      expect(result.current.status).toBe("failed");
    });
    expect(result.current.options).toEqual([]);
  });

  it("is empty when every repository is hidden", async () => {
    serveRepos([], ["acme/secret"]);
    const { result } = renderTargets("repo");
    await settled(result);
    expect(result.current.status).toBe("empty");
  });
});

describe("useOperatorTargets — the workspace scope", () => {
  it("asks for the triage view and keeps only the rows the service marks stuck", async () => {
    serveRepos(["acme/widget"]);
    const seen = serveWorkspaces([
      stuck("fix-it"),
      makeWorkspaceSummary({ task_id: "healthy", title: "Healthy", operator_link: false }),
      stuck("broken", { status: "failed", engine_key: "acme/gadget" }),
    ]);
    const { result } = renderTargets("workspace");
    await settled(result);
    expect(seen.includes).toEqual(["triage"]);
    expect(result.current.status).toBe("ready");
    expect(result.current.options).toEqual([
      { value: "fix-it", label: "Title of fix-it · escalated · acme/widget" },
      { value: "broken", label: "Title of broken · failed · acme/gadget" },
    ]);
  });

  it("leaves the repository out of a label when the workspace has none", async () => {
    serveWorkspaces([stuck("fix-it", { engine_key: null })]);
    const { result } = renderTargets("workspace");
    await settled(result);
    expect(labels(result)).toEqual(["Title of fix-it · escalated"]);
  });

  it("does not preselect a workspace when none was named", async () => {
    serveWorkspaces([stuck("fix-it")]);
    const { result } = renderTargets("workspace");
    await settled(result);
    expect(result.current.initial).toBe("");
  });

  it("preselects a named workspace that is stuck without listing it twice", async () => {
    serveWorkspaces([stuck("fix-it"), stuck("other")]);
    const { result } = renderTargets("workspace", "fix-it");
    await settled(result);
    expect(result.current.initial).toBe("fix-it");
    expect(result.current.options.map((option) => option.value)).toEqual(["fix-it", "other"]);
  });

  it("keeps a named workspace selectable after it stopped being stuck", async () => {
    serveWorkspaces([
      stuck("other"),
      makeWorkspaceSummary({
        task_id: "fix-it",
        title: "Fixed itself",
        status: "completed",
        operator_link: false,
        engine_key: "acme/widget",
      }),
    ]);
    const { result } = renderTargets("workspace", "fix-it");
    await settled(result);
    expect(result.current.initial).toBe("fix-it");
    expect(result.current.options).toContainEqual({
      value: "fix-it",
      label: "Fixed itself · completed · acme/widget",
    });
  });

  it("pins a bare-id option for a named workspace the list does not carry", async () => {
    serveWorkspaces([stuck("other")]);
    const { result } = renderTargets("workspace", "gone-ws");
    await settled(result);
    expect(result.current.initial).toBe("gone-ws");
    expect(result.current.options).toEqual([
      { value: "gone-ws", label: "gone-ws" },
      { value: "other", label: "Title of other · escalated · acme/widget" },
    ]);
  });

  it("does not pin a name that is not a workspace id", async () => {
    serveWorkspaces([stuck("other")]);
    const { result } = renderTargets("workspace", "acme/widget");
    await settled(result);
    expect(result.current.initial).toBe("");
    expect(labels(result)).toEqual(["Title of other · escalated · acme/widget"]);
  });

  it("is empty when nothing is stuck and nothing is named", async () => {
    serveWorkspaces([makeWorkspaceSummary({ task_id: "healthy", operator_link: false })]);
    const { result } = renderTargets("workspace");
    await settled(result);
    expect(result.current.status).toBe("empty");
    expect(result.current.options).toEqual([]);
  });

  it("is loading until the workspace list answers", async () => {
    const gate = deferredFetch();
    server.use(
      http.get("/api/workspaces", async () => {
        await gate.settled;
        return HttpResponse.json([stuck("fix-it")]);
      }),
    );
    const { result } = renderTargets("workspace", "fix-it");
    expect(result.current.status).toBe("loading");
    gate.release();
    await settled(result);
    expect(result.current.status).toBe("ready");
  });

  it("fails when the workspace list cannot be read", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderTargets("workspace", "fix-it");
    await waitFor(() => {
      expect(result.current.status).toBe("failed");
    });
  });

  it("asks for the workspace list only once the workspace scope is chosen", async () => {
    serveRepos(["acme/widget"]);
    const seen = serveWorkspaces([stuck("fix-it")]);
    const { result, rerender } = renderTargets("repo");
    await settled(result);
    expect(seen.calls).toBe(0);
    rerender({ scope: "workspace", preselected: "" });
    await waitFor(() => {
      expect(result.current.options).toHaveLength(1);
    });
    expect(seen.calls).toBe(1);
  });
});
