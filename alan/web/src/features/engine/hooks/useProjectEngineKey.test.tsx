import { waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { makeProjectDetail } from "../../../test/projectFixtures";
import { renderHookWithQuery } from "../../../test/renderWithQuery";
import { server } from "../../../test/server";
import { useProjectEngineKey } from "./useProjectEngineKey";

function serveProject(engineKey: string | null) {
  server.use(
    http.get("/api/projects/alpha", () =>
      HttpResponse.json(makeProjectDetail({ id: "alpha", engine_key: engineKey })),
    ),
  );
}

describe("useProjectEngineKey", () => {
  it("starts in the loading state", () => {
    serveProject("acme/widget");
    const { result } = renderHookWithQuery(() => useProjectEngineKey("alpha"));
    expect(result.current).toEqual({ status: "loading" });
  });

  it("reports the project's bound engine key once it resolves", async () => {
    serveProject("acme/widget");
    const { result } = renderHookWithQuery(() => useProjectEngineKey("alpha"));
    await waitFor(() => {
      expect(result.current).toEqual({ status: "ready", engineKey: "acme/widget" });
    });
  });

  it("reports a null engine key for a project that is not bound to an engine", async () => {
    serveProject(null);
    const { result } = renderHookWithQuery(() => useProjectEngineKey("alpha"));
    await waitFor(() => {
      expect(result.current).toEqual({ status: "ready", engineKey: null });
    });
  });

  it("surfaces a failed read as the error state carrying the server message", async () => {
    server.use(
      http.get("/api/projects/alpha", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const { result } = renderHookWithQuery(() => useProjectEngineKey("alpha"));
    await waitFor(() => {
      expect(result.current.status).toBe("error");
    });
    if (result.current.status !== "error") throw new Error("expected error");
    expect(result.current.message).toContain("boom");
  });

  it("stays in the loading state and issues no read while the project id is undefined", async () => {
    const tally = { calls: 0 };
    server.use(
      http.get("/api/projects/alpha", () => {
        tally.calls += 1;
        return HttpResponse.json(makeProjectDetail({ id: "alpha" }));
      }),
    );
    const { result } = renderHookWithQuery(() => useProjectEngineKey(undefined));
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 10);
    });
    expect(result.current).toEqual({ status: "loading" });
    expect(tally.calls).toBe(0);
  });
});
