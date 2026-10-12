import { act, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { deferredFetch } from "../../../test/docReadHarness";
import { renderHookWithQuery } from "../../../test/renderWithQuery";
import { server } from "../../../test/server";
import { useProjectDoc, type UseProjectDocResult } from "./useProjectDoc";

const DOC = { vision: "v", scope: "s", decision_log: "d", discovered_facts: "f" };

function serveDoc(projectId: string, respond: () => Response) {
  const tally = { calls: 0 };
  server.use(
    http.get(`/api/projects/${projectId}/doc`, () => {
      tally.calls += 1;
      return respond();
    }),
  );
  return tally;
}

async function refetchNow(result: { current: UseProjectDocResult }): Promise<void> {
  if (result.current.status === "loading") throw new Error("expected a refetch arm");
  const { refetch } = result.current;
  await act(async () => {
    refetch();
    await Promise.resolve();
  });
}

describe("useProjectDoc", () => {
  it("starts in the loading state and reports the document ready once it resolves", async () => {
    serveDoc("alpha", () => HttpResponse.json(DOC));
    const { result } = renderHookWithQuery(() => useProjectDoc("alpha"));
    expect(result.current.status).toBe("loading");
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
    if (result.current.status !== "ready") throw new Error("expected ready");
    expect(result.current.doc).toEqual(DOC);
  });

  it("surfaces a failed read as the error state carrying the server message", async () => {
    serveDoc("alpha", () => HttpResponse.json({ detail: "boom" }, { status: 500 }));
    const { result } = renderHookWithQuery(() => useProjectDoc("alpha"));
    await waitFor(() => {
      expect(result.current.status).toBe("error");
    });
    if (result.current.status !== "error") throw new Error("expected error");
    expect(result.current.message).toContain("boom");
  });

  it("stays in the loading state, not an error, and issues no read while the project id is undefined", async () => {
    const tally = serveDoc("alpha", () => HttpResponse.json(DOC));
    const { result } = renderHookWithQuery(() => useProjectDoc(undefined));
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 10);
    });
    expect(result.current.status).toBe("loading");
    expect(tally.calls).toBe(0);
  });

  it("reads once the project id resolves after being undefined", async () => {
    const tally = serveDoc("alpha", () => HttpResponse.json(DOC));
    const { result, rerender } = renderHookWithQuery(
      ({ projectId }: { projectId: string | undefined }) => useProjectDoc(projectId),
      { initialProps: { projectId: undefined as string | undefined } },
    );
    expect(result.current.status).toBe("loading");

    rerender({ projectId: "alpha" });
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
    expect(tally.calls).toBe(1);
  });

  it.each([
    ["ready", () => HttpResponse.json(DOC)],
    ["error", () => HttpResponse.json({ detail: "boom" }, { status: 500 })],
  ] as const)(
    "refresh from the %s state lands on loading before the re-read settles",
    async (status, first) => {
      const gate = deferredFetch();
      let served = 0;
      server.use(
        http.get("/api/projects/alpha/doc", async () => {
          served += 1;
          if (served === 1) return first();
          await gate.settled;
          return HttpResponse.json({ ...DOC, vision: "refreshed" });
        }),
      );
      const { result } = renderHookWithQuery(() => useProjectDoc("alpha"));
      await waitFor(() => {
        expect(result.current.status).toBe(status);
      });

      await refetchNow(result);
      expect(result.current.status).toBe("loading");

      gate.release();
      await waitFor(() => {
        expect(result.current.status).toBe("ready");
      });
      if (result.current.status !== "ready") throw new Error("expected ready");
      expect(result.current.doc.vision).toBe("refreshed");
    },
  );
});
