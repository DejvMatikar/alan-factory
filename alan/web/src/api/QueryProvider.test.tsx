import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../test/server";
import { renderHookWithQuery, renderWithQuery } from "../test/renderWithQuery";
import { queryClient } from "./queryClient";
import { useHealthQuery } from "./queries";

const HEALTHY = { ok: true, queue_paused: false, paused_until: null, health_state: null };

describe("QueryProvider", () => {
  it("renders its children", () => {
    renderWithQuery(<span data-testid="child">hello</span>);
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("mounts the shared client so a read made beneath it lands in the singleton cache", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    const { result } = renderHookWithQuery(() => useHealthQuery());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(queryClient.getQueryData(["health", "context"])).toEqual(HEALTHY);
  });

  it("hands renderHookWithQuery's callback the props it was given", () => {
    const { result } = renderHookWithQuery(({ label }: { label: string }) => label, {
      initialProps: { label: "probe" },
    });
    expect(result.current).toBe("probe");
  });
});
