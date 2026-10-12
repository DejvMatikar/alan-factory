import { waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { makeEngineConfig } from "../test/engineFixtures";
import { deferredFetch, flushGc, type DeferredFetch } from "../test/docReadHarness";
import { makeProjectDetail } from "../test/projectFixtures";
import { renderHookWithQuery } from "../test/renderWithQuery";
import { server } from "../test/server";
import { useInvocationDocs } from "../features/workspace/hooks/useInvocationDocs";
import { useEngineConfigDoc } from "../features/engine/hooks/useEngineConfigDoc";
import { useProjectDoc } from "../features/project/hooks/useProjectDoc";
import { useProjectEngineKey } from "../features/engine/hooks/useProjectEngineKey";

interface ReadView {
  status: string;
  content: string | null;
}

interface DocRead {
  name: string;
  path: (key: string) => string;
  payload: (key: string) => Record<string, unknown>;
  contentOf: (key: string) => string;
  useRead: (key: string) => ReadView;
  companions?: () => void;
}

const INVOCATION_DOC = {
  schema_version: "1.0",
  id: "inv_001",
  namespace: "alan.workspace.t1",
  status: "success",
  started_at: "2026-05-12T08:00:00Z",
  completed_at: "2026-05-12T08:00:30Z",
  tasks: [],
};

const READS: readonly DocRead[] = [
  {
    name: "useProjectDoc",
    path: (key) => `/api/projects/${key}/doc`,
    payload: (key) => ({
      vision: `vision of ${key}`,
      scope: "scope",
      decision_log: "log",
      discovered_facts: "facts",
    }),
    contentOf: (key) => `vision of ${key}`,
    useRead: (key) => {
      const state = useProjectDoc(key);
      return { status: state.status, content: state.status === "ready" ? state.doc.vision : null };
    },
  },
  {
    name: "useEngineConfigDoc",
    path: (key) => `/api/engines/acme/${key}`,
    payload: (key) => makeEngineConfig({ repo: { owner: "acme", repo: key } }),
    contentOf: (key) => key,
    useRead: (key) => {
      const state = useEngineConfigDoc(`acme/${key}`);
      return { status: state.status, content: state.status === "ready" ? state.config.repo.repo : null };
    },
    companions: () => {
      server.use(http.get("/api/engines/sources", () => HttpResponse.json({ sources: [] })));
    },
  },
  {
    name: "useProjectEngineKey",
    path: (key) => `/api/projects/${key}`,
    payload: (key) => makeProjectDetail({ id: key, engine_key: `acme/${key}` }),
    contentOf: (key) => `acme/${key}`,
    useRead: (key) => {
      const state = useProjectEngineKey(key);
      return {
        status: state.status,
        content: state.status === "ready" ? (state.engineKey ?? null) : null,
      };
    },
  },
  {
    name: "useInvocationDocs",
    path: (key) => `/api/workspaces/${key}/invocations/inv_001`,
    payload: (key) => ({ ...INVOCATION_DOC, team: `team-${key}` }),
    contentOf: (key) => `team-${key}`,
    useRead: (key) => {
      const state = useInvocationDocs(key, ["inv_001"]);
      return {
        status: state.allSettled ? "ready" : "loading",
        content: state.docs.get("inv_001")?.team ?? null,
      };
    },
  },
];

function serveKeys(read: DocRead, keys: readonly string[], gates: Record<string, DeferredFetch> = {}) {
  const calls: Record<string, number> = {};
  read.companions?.();
  for (const key of keys) {
    calls[key] = 0;
    server.use(
      http.get(read.path(key), async () => {
        calls[key] = (calls[key] ?? 0) + 1;
        await gates[key]?.settled;
        return HttpResponse.json(read.payload(key));
      }),
    );
  }
  return calls;
}

function mountRead(read: DocRead, key: string) {
  return renderHookWithQuery(({ current }: { current: string }) => read.useRead(current), {
    initialProps: { current: key },
  });
}

async function settled(result: { current: ReadView }, content: string): Promise<void> {
  await waitFor(() => {
    expect(result.current).toEqual({ status: "ready", content });
  });
}

describe.each(READS)("$name — defaults opt-out", (read) => {
  it("lands on loading with the previous key's content absent when the key changes", async () => {
    const gateB = deferredFetch();
    serveKeys(read, ["a", "b"], { b: gateB });
    const { result, rerender } = mountRead(read, "a");
    await settled(result, read.contentOf("a"));

    rerender({ current: "b" });
    await flushGc();
    expect(result.current).toEqual({ status: "loading", content: null });

    gateB.release();
    await settled(result, read.contentOf("b"));
  });

  it("returns to loading and re-reads when a key is revisited after its entry was collected", async () => {
    const calls = serveKeys(read, ["a", "b"]);
    const { result, rerender } = mountRead(read, "a");
    await settled(result, read.contentOf("a"));
    rerender({ current: "b" });
    await settled(result, read.contentOf("b"));
    await flushGc();

    rerender({ current: "a" });
    expect(result.current).toEqual({ status: "loading", content: null });
    await settled(result, read.contentOf("a"));
    expect(calls.a).toBe(2);
  });

  it("lands on loading and re-reads when remounted after its entry was collected", async () => {
    const calls = serveKeys(read, ["a"]);
    const first = mountRead(read, "a");
    await settled(first.result, read.contentOf("a"));
    first.unmount();
    await flushGc();

    const second = mountRead(read, "a");
    expect(second.result.current).toEqual({ status: "loading", content: null });
    await settled(second.result, read.contentOf("a"));
    expect(calls.a).toBe(2);
  });

  it("re-reads on a remount that lands before its entry was collected", async () => {
    const calls = serveKeys(read, ["a"]);
    const first = mountRead(read, "a");
    await settled(first.result, read.contentOf("a"));

    first.unmount();
    const second = mountRead(read, "a");
    await waitFor(() => {
      expect(calls.a).toBe(2);
    });
    await settled(second.result, read.contentOf("a"));
  });

  it("re-reads when a key is revisited before its entry was collected", async () => {
    const calls = serveKeys(read, ["a", "b"]);
    const { result, rerender } = mountRead(read, "a");
    await settled(result, read.contentOf("a"));

    rerender({ current: "b" });
    rerender({ current: "a" });
    await waitFor(() => {
      expect(calls.a).toBe(2);
    });
    await settled(result, read.contentOf("a"));
  });
});
