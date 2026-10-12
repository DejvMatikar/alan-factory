import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import {
  BUILD_CHAT_PATH,
  SID,
  defaultReadyProposal,
  sessionResp,
} from "../../../test/buildChatPageHarness";
import BuildChat from "./BuildChat";

function ScopedBuildChat() {
  const { owner, repo } = useParams<{ owner: string; repo: string }>();
  return (
    <>
      <div data-testid="scoped-owner-repo">{`${owner}/${repo}`}</div>
      <BuildChat />
    </>
  );
}

describe("BuildChat — scoped-route engine self-correction", () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => HttpResponse.json(defaultReadyProposal())),
    );
  });

  it("redirects to the session's real repository when it differs from the URL's :owner/:repo", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(sessionResp({ engine_key: "acme/other" })),
      ),
    );
    render(
      <MemoryRouter initialEntries={[`/repos/acme/widget/build?s=${SID}`]}>
        <Routes>
          <Route path="/repos/:owner/:repo/build" element={<ScopedBuildChat />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("scoped-owner-repo")).toHaveTextContent("acme/other"));
  });

  it("stays put when the session's engine_key already matches the URL scope", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(sessionResp({ engine_key: "acme/widget" })),
      ),
    );
    render(
      <MemoryRouter initialEntries={[`/repos/acme/widget/build?s=${SID}`]}>
        <Routes>
          <Route path="/repos/:owner/:repo/build" element={<ScopedBuildChat />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.getByTestId("scoped-owner-repo")).toHaveTextContent("acme/widget");
  });
});
