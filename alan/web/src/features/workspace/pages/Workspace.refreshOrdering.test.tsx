import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import { makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import Workspace from "./Workspace";

const RETRY_FRAME =
  'event: job_retry_scheduled\ndata: {"team":"research","job_id":"5","attempt":"1","eligible_at":"9999999999.0"}\n\n';

describe("Workspace refresh ordering", () => {
  it("ignores an invocation list that resolves after a newer refresh began", async () => {
    let listCalls = 0;
    let releaseFirstList: (() => void) | null = null;
    let releaseEvents: (() => void) | null = null;
    const staleDocRequests: string[] = [];
    server.use(
      http.get(/\/api\/workspaces\/[^/]+\/dispatch-instructions$/, () => HttpResponse.json({})),
      http.get(/\/api\/workspaces\/[^/]+\/sticky-instructions$/, () =>
        HttpResponse.json({ text: null, origin: null }),
      ),
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(makeWorkspaceDetail({ title: "T1 Title" })),
      ),
      http.get("/api/workspaces/t1/invocations", () => {
        listCalls += 1;
        if (listCalls > 1) return HttpResponse.json([]);
        return new Promise<Response>((resolve) => {
          releaseFirstList = () => resolve(HttpResponse.json(["inv_stale"]));
        });
      }),
      http.get("/api/workspaces/t1/invocations/inv_stale", () => {
        staleDocRequests.push("inv_stale");
        return HttpResponse.json({ detail: "not found" }, { status: 404 });
      }),
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new Promise<Response>((resolve) => {
            releaseEvents = () =>
              resolve(
                new HttpResponse(RETRY_FRAME, { headers: { "Content-Type": "text/event-stream" } }),
              );
          }),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/workspaces/t1"]}>
        <Routes>
          <Route path="/workspaces/:taskId" element={<Workspace />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(releaseFirstList).not.toBeNull());
    await waitFor(() => expect(releaseEvents).not.toBeNull());
    releaseEvents!();
    await waitFor(() => expect(listCalls).toBe(2));
    releaseFirstList!();
    await new Promise((r) => setTimeout(r, 50));
    expect(staleDocRequests).toEqual([]);
    expect(screen.getByRole("heading", { level: 1, name: "T1 Title" })).toBeInTheDocument();
  });
});
