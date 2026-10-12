import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import ExploreChat from "./ExploreChat";

const SESSIONS_PATH = "/api/explore-chat/sessions";
const CONSULTANTS_PATH = "/api/explore-chat/consultants";

function renderAt(consultant: string) {
  return render(
    <MemoryRouter initialEntries={[`/explore/${consultant}`]}>
      <Routes>
        <Route path="/explore/:consultant" element={<ExploreChat />} />
      </Routes>
    </MemoryRouter>,
  );
}

function trackSessionPosts(): unknown[] {
  const posted: unknown[] = [];
  server.use(
    http.post(SESSIONS_PATH, async ({ request }) => {
      posted.push(await request.json());
      return HttpResponse.json(
        { session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" },
        { status: 201 },
      );
    }),
  );
  return posted;
}

describe("ExploreChat consultant gate", () => {
  it("shows a plain loading line with no alert role and creates no session while the list is still loading", async () => {
    server.use(http.get(CONSULTANTS_PATH, () => new Promise<Response>(() => undefined)));
    const posted = trackSessionPosts();
    renderAt("brainstorming");
    expect(await screen.findByTestId("explore-consultants-loading")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(posted).toHaveLength(0);
  });

  it("shows no alert while loading and Unknown consultant once the list has loaded for an unknown key, creating no session", async () => {
    let resolveConsultants!: () => void;
    const consultantsPromise = new Promise<Response>((res) => {
      resolveConsultants = () => res(HttpResponse.json([]));
    });
    server.use(http.get(CONSULTANTS_PATH, () => consultantsPromise));
    const posted = trackSessionPosts();
    renderAt("mystery_key");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    resolveConsultants();
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown consultant");
    expect(posted).toHaveLength(0);
  });

  it("shows the shared load-error banner and creates no session when the consultants fetch fails", async () => {
    server.use(
      http.get(CONSULTANTS_PATH, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const posted = trackSessionPosts();
    renderAt("brainstorming");
    expect(await screen.findByTestId("explore-consultants-load-error")).toBeInTheDocument();
    expect(posted).toHaveLength(0);
  });

  it("shows the consultant's label heading and creates exactly one session once the list has loaded for a known key", async () => {
    const posted = trackSessionPosts();
    renderAt("brainstorming");
    expect(
      await screen.findByRole("heading", { level: 1, name: /Explore \/ Brainstorming/ }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(posted).toHaveLength(1);
    });
  });
});
