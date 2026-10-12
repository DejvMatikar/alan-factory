import { act, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { EngineSource } from "../../../api";
import { deferredFetch } from "../../../test/docReadHarness";
import { makeEngineConfig } from "../../../test/engineFixtures";
import { renderHookWithQuery } from "../../../test/renderWithQuery";
import { server } from "../../../test/server";
import { useEngineConfigDoc, type UseEngineConfigDocResult } from "./useEngineConfigDoc";

const CONFIG = makeEngineConfig();

async function refetchNow(result: { current: UseEngineConfigDocResult }): Promise<void> {
  if (result.current.status === "loading") throw new Error("expected a refetch arm");
  const { refetch } = result.current;
  await act(async () => {
    refetch();
    await Promise.resolve();
  });
}

describe("useEngineConfigDoc", () => {
  it("starts in the loading state", () => {
    server.use(http.get("/api/engines/acme/widget", () => HttpResponse.json(CONFIG)));
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    expect(result.current.status).toBe("loading");
  });

  it("does nothing while engineKey is undefined", () => {
    const { result } = renderHookWithQuery(() => useEngineConfigDoc(undefined));
    expect(result.current.status).toBe("loading");
  });

  it("loads the full engine config and reports it ready, with a null source when no sources entry matches", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json(CONFIG)),
      http.get("/api/engines/sources", () => HttpResponse.json({ sources: [] })),
    );
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
    if (result.current.status !== "ready") throw new Error("expected ready");
    expect(result.current.config).toEqual(CONFIG);
    expect(result.current.source).toBeNull();
  });

  it("exposes the matching sources entry's config_path and in_version_control flag", async () => {
    const source: EngineSource = {
      slug: "acme/widget",
      in_version_control: true,
      config_path: "config/engines.d/widget.yaml",
      posture_ceiling: "propose",
      export_command: null,
    };
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json(CONFIG)),
      http.get("/api/engines/sources", () =>
        HttpResponse.json({
          sources: [
            source,
            { ...source, slug: "other/repo", config_path: "config/engines.d/repo.yaml" },
          ],
        }),
      ),
    );
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
    if (result.current.status !== "ready") throw new Error("expected ready");
    expect(result.current.source).toEqual(source);
  });

  it("treats a sources-fetch failure as an unknown (null) source rather than an error", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json(CONFIG)),
      http.get("/api/engines/sources", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
    if (result.current.status !== "ready") throw new Error("expected ready");
    expect(result.current.source).toBeNull();
  });

  it("classifies a 404 from the config fetch as the empty state, not an error", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ detail: "engine 'acme/widget' not registered" }, { status: 404 }),
      ),
    );
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("empty");
    });
  });

  it("surfaces a non-404 API error as the error state", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("error");
    });
    if (result.current.status !== "error") throw new Error("expected error");
    expect(result.current.message).toContain("boom");
  });

  it("surfaces a non-API failure (e.g. a network error) as the error state, not empty", async () => {
    server.use(http.get("/api/engines/acme/widget", () => HttpResponse.error()));
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("error");
    });
  });

  it("refetch re-issues the request and can recover from an error", async () => {
    let calls = 0;
    server.use(
      http.get("/api/engines/acme/widget", () => {
        calls += 1;
        if (calls === 1) return HttpResponse.json({ detail: "boom" }, { status: 500 });
        return HttpResponse.json(CONFIG);
      }),
    );
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("error");
    });
    if (result.current.status !== "error") throw new Error("expected error");
    result.current.refetch();
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
    expect(calls).toBe(2);
  });

  it("refetch from the empty state can recover once the engine is registered", async () => {
    let calls = 0;
    server.use(
      http.get("/api/engines/acme/widget", () => {
        calls += 1;
        if (calls === 1) return HttpResponse.json({ detail: "not found" }, { status: 404 });
        return HttpResponse.json(CONFIG);
      }),
      http.get("/api/engines/sources", () => HttpResponse.json({ sources: [] })),
    );
    const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("empty");
    });
    if (result.current.status !== "empty") throw new Error("expected empty");
    result.current.refetch();
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
    expect(calls).toBe(2);
  });

  it("does not update state after unmount", async () => {
    server.use(http.get("/api/engines/acme/widget", () => HttpResponse.json(CONFIG)));
    const { unmount } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    unmount();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("does not update state after unmount when the in-flight request errors", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const { unmount } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
    unmount();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  it("lands on loading and never renders one repository's config under another when the key changes", async () => {
    const gate = deferredFetch();
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json(CONFIG)),
      http.get("/api/engines/acme/gadget", async () => {
        await gate.settled;
        return HttpResponse.json(makeEngineConfig({ repo: { owner: "acme", repo: "gadget" } }));
      }),
      http.get("/api/engines/sources", () => HttpResponse.json({ sources: [] })),
    );
    const { result, rerender } = renderHookWithQuery(
      ({ engineKey }: { engineKey: string }) => useEngineConfigDoc(engineKey),
      { initialProps: { engineKey: "acme/widget" } },
    );
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });

    rerender({ engineKey: "acme/gadget" });
    expect(result.current.status).toBe("loading");

    gate.release();
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
    if (result.current.status !== "ready") throw new Error("expected ready");
    expect(result.current.config.repo.repo).toBe("gadget");
  });

  it.each([
    ["ready", () => HttpResponse.json(CONFIG)],
    ["empty", () => HttpResponse.json({ detail: "not found" }, { status: 404 })],
    ["error", () => HttpResponse.json({ detail: "boom" }, { status: 500 })],
  ] as const)(
    "refresh from the %s state lands on loading before the re-read settles",
    async (status, first) => {
      const gate = deferredFetch();
      let served = 0;
      server.use(
        http.get("/api/engines/acme/widget", async () => {
          served += 1;
          if (served === 1) return first();
          await gate.settled;
          return HttpResponse.json(CONFIG);
        }),
        http.get("/api/engines/sources", () => HttpResponse.json({ sources: [] })),
      );
      const { result } = renderHookWithQuery(() => useEngineConfigDoc("acme/widget"));
      await waitFor(() => {
        expect(result.current.status).toBe(status);
      });

      await refetchNow(result);
      expect(result.current.status).toBe("loading");

      gate.release();
      await waitFor(() => {
        expect(result.current.status).toBe("ready");
      });
    },
  );
});
