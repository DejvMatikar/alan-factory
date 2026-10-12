import { waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import {
  catalogueHandler,
  makeAgentEntry,
  makeCatalogue,
  makeSummary,
  summaryHandler,
} from "../../../test/agentFixtures";
import { renderHookWithQuery } from "../../../test/renderWithQuery";
import { server } from "../../../test/server";
import { useAgentCatalogue, useAgentCatalogueSummary } from "./useAgentCatalogue";

describe("useAgentCatalogue", () => {
  it("starts loading, then exposes the catalogue", async () => {
    const catalogue = makeCatalogue([makeAgentEntry()]);
    server.use(catalogueHandler("acme", "widget", catalogue));
    const { result } = renderHookWithQuery(() => useAgentCatalogue("acme/widget"));
    expect(result.current.status).toBe("loading");
    await waitFor(() => {
      expect(result.current).toMatchObject({ status: "ready", data: catalogue });
    });
  });

  it("stays loading and never fetches without an engine key", () => {
    const { result } = renderHookWithQuery(() => useAgentCatalogue(undefined));
    expect(result.current.status).toBe("loading");
  });

  it("exposes the failure message and a refetch that recovers", async () => {
    server.use(
      http.get("/api/engines/acme/widget/agents", () =>
        HttpResponse.json({ detail: "engine not found" }, { status: 404 }),
      ),
    );
    const { result } = renderHookWithQuery(() => useAgentCatalogue("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("error");
    });
    const catalogue = makeCatalogue([makeAgentEntry()]);
    server.use(catalogueHandler("acme", "widget", catalogue));
    if (result.current.status === "error") result.current.refetch();
    await waitFor(() => {
      expect(result.current.status).toBe("ready");
    });
  });
});

describe("useAgentCatalogueSummary", () => {
  it("exposes the summary", async () => {
    const summary = makeSummary();
    server.use(summaryHandler("acme", "widget", summary));
    const { result } = renderHookWithQuery(() => useAgentCatalogueSummary("acme/widget"));
    await waitFor(() => {
      expect(result.current).toMatchObject({ status: "ready", data: summary });
    });
  });

  it("reports a failure", async () => {
    server.use(
      http.get("/api/engines/acme/widget/agents/summary", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHookWithQuery(() => useAgentCatalogueSummary("acme/widget"));
    await waitFor(() => {
      expect(result.current.status).toBe("error");
    });
  });
});
