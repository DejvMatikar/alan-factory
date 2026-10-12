import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import {
  BASE,
  COUNTER_CASE,
  SID,
  readyProposal,
  sessionResp,
} from "../../../test/exploreChatPageHarness";
import ExploreChat from "./ExploreChat";

function ScopedExploreChat() {
  const { owner, repo } = useParams<{ owner: string; repo: string }>();
  return (
    <>
      <div data-testid="scoped-owner-repo">{`${owner}/${repo}`}</div>
      <ExploreChat />
    </>
  );
}

describe("ExploreChat — scoped-route engine self-correction", () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => HttpResponse.json(readyProposal(COUNTER_CASE))),
    );
  });

  it("redirects to the session's real repository when it differs from the URL's :owner/:repo", async () => {
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(sessionResp({ engine_key: "acme/other" })),
      ),
    );
    render(
      <MemoryRouter initialEntries={[`/repos/acme/widget/explore/brainstorming?s=${SID}`]}>
        <Routes>
          <Route path="/repos/:owner/:repo/explore/:consultant" element={<ScopedExploreChat />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("scoped-owner-repo")).toHaveTextContent("acme/other"));
  });

  it("stays put when the session's engine_key already matches the URL scope", async () => {
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(sessionResp({ engine_key: "acme/widget" })),
      ),
    );
    render(
      <MemoryRouter initialEntries={[`/repos/acme/widget/explore/brainstorming?s=${SID}`]}>
        <Routes>
          <Route path="/repos/:owner/:repo/explore/:consultant" element={<ScopedExploreChat />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.getByTestId("scoped-owner-repo")).toHaveTextContent("acme/widget");
  });
});
