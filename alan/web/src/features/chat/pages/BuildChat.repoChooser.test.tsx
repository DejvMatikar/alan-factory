import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { STUB_DEFAULT_ENGINE_KEY } from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import {
  BUILD_CHAT_PATH,
  SID,
  defaultReadyProposal,
  installBuildChatPageSetup,
  renderBuildChat,
  sessionResp,
} from "../../../test/buildChatPageHarness";

describe("BuildChat", () => {
  installBuildChatPageSetup();

  describe("Repo chooser (repo-visibility-and-selector)", () => {
    function stubEngines(repos: { owner: string; repo: string }[]) {
      server.use(
        http.get("/api/engines", () =>
          HttpResponse.json({
            default_engine_key: STUB_DEFAULT_ENGINE_KEY,
            engines: repos.map((r) => ({ repo: r, posture: "autonomous" })),
          }),
        ),
      );
    }

    it("shows no chooser and creates a session immediately with only the default repo registered", async () => {
      stubEngines([]);
      renderBuildChat();
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    });

    it("shows a chooser with more than one repository connected, gates session creation, and binds the pick", async () => {
      let createCalls = 0;
      let capturedBody: { project_id?: string; engine_key?: string } | null = null;
      server.use(
        http.post("/api/build-chat/sessions", async ({ request }) => {
          createCalls += 1;
          capturedBody = (await request.json()) as { project_id?: string; engine_key?: string };
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
      );
      stubEngines([{ owner: "acme-org", repo: "widgets" }]);
      renderBuildChat();
      const chooser = await screen.findByTestId("repo-chooser-modal");
      expect(screen.getByTestId("repo-option-DejvMatikar/alan")).toBeInTheDocument();
      expect(screen.getByTestId("repo-option-acme-org/widgets")).toBeInTheDocument();
      expect(createCalls).toBe(0);

      const user = userEvent.setup();
      await user.click(screen.getByTestId("repo-option-acme-org/widgets"));
      await waitFor(() => expect(chooser).not.toBeInTheDocument());
      await waitFor(() => expect(createCalls).toBe(1));
      expect(capturedBody).toEqual({ engine_key: "acme-org/widgets" });
    });

    it("closing the chooser navigates home without creating a session", async () => {
      let createCalls = 0;
      server.use(
        http.post("/api/build-chat/sessions", () => {
          createCalls += 1;
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
      );
      stubEngines([{ owner: "acme-org", repo: "widgets" }]);
      renderBuildChat();
      await screen.findByTestId("repo-chooser-modal");
      const user = userEvent.setup();
      await user.click(screen.getByTestId("modal-close"));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(createCalls).toBe(0);
    });

    it("does not show the chooser when resuming a session via ?s= with no ?engine=", async () => {
      let createCalls = 0;
      server.use(
        http.post("/api/build-chat/sessions", () => {
          createCalls += 1;
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
      );
      stubEngines([{ owner: "acme-org", repo: "widgets" }]);
      renderBuildChat(`/build?s=${SID}`);
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
      expect(createCalls).toBe(0);
    });

    it("a repo-scoped entry point arriving with a valid ?engine= skips the chooser and binds immediately", async () => {
      let createCalls = 0;
      let capturedBody: { project_id?: string; engine_key?: string } | null = null;
      server.use(
        http.post("/api/build-chat/sessions", async ({ request }) => {
          createCalls += 1;
          capturedBody = (await request.json()) as { project_id?: string; engine_key?: string };
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
      );
      stubEngines([{ owner: "acme-org", repo: "widgets" }]);
      renderBuildChat("/build?engine=acme-org%2Fwidgets");
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
      expect(createCalls).toBe(1);
      expect(capturedBody).toEqual({ engine_key: "acme-org/widgets" });
    });

    it("treats an ?engine= value that names no connected repository as unset, still showing the chooser", async () => {
      stubEngines([{ owner: "acme-org", repo: "widgets" }]);
      renderBuildChat("/build?engine=not-a-connected-repo");
      const chooser = await screen.findByTestId("repo-chooser-modal");
      expect(chooser).toBeInTheDocument();
    });

    it("treats an empty ?engine= value as unset, still showing the chooser", async () => {
      stubEngines([{ owner: "acme-org", repo: "widgets" }]);
      renderBuildChat("/build?engine=");
      const chooser = await screen.findByTestId("repo-chooser-modal");
      expect(chooser).toBeInTheDocument();
    });

    it("starts no session and shows no chooser when the engines fetch fails, since no default repository is known", async () => {
      let createCalls = 0;
      let engineRequests = 0;
      server.use(
        http.post("/api/build-chat/sessions", () => {
          createCalls += 1;
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
        http.get("/api/engines", () => {
          engineRequests += 1;
          return HttpResponse.json({ detail: "boom" }, { status: 500 });
        }),
      );
      renderBuildChat();
      await waitFor(() => expect(engineRequests).toBeGreaterThan(0));
      await new Promise((r) => setTimeout(r, 20));
      expect(createCalls).toBe(0);
      expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    });

    it("starts no session when no repository is registered and the list names no default", async () => {
      let createCalls = 0;
      let engineRequests = 0;
      server.use(
        http.post("/api/build-chat/sessions", () => {
          createCalls += 1;
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
        http.get("/api/engines", () => {
          engineRequests += 1;
          return HttpResponse.json({ engines: [] });
        }),
      );
      renderBuildChat();
      await waitFor(() => expect(engineRequests).toBeGreaterThan(0));
      await new Promise((r) => setTimeout(r, 20));
      expect(createCalls).toBe(0);
      expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    });

    it("ignores an engines-fetch rejection that resolves after unmount", async () => {
      let reject: ((reason?: unknown) => void) | null = null;
      server.use(
        http.get(
          "/api/engines",
          () =>
            new Promise<Response>((_res, rej) => {
              reject = rej;
            }),
        ),
      );
      const { unmount } = renderBuildChat();
      await new Promise((r) => setTimeout(r, 5));
      unmount();
      reject!(new Error("late engines failure"));
      await new Promise((r) => setTimeout(r, 10));
    });

    it("ignores an engines-fetch success that resolves after unmount", async () => {
      let resolve: ((value: Response) => void) | null = null;
      server.use(
        http.get(
          "/api/engines",
          () =>
            new Promise<Response>((res) => {
              resolve = res;
            }),
        ),
      );
      const { unmount } = renderBuildChat();
      await new Promise((r) => setTimeout(r, 5));
      unmount();
      resolve!(HttpResponse.json({ engines: [] }));
      await new Promise((r) => setTimeout(r, 10));
    });
  });

  describe("Repository-scoped entry via ?project= (binds without opening the chooser)", () => {
    function projectEntry(id: string, title: string, engineKey: string | null = null) {
      return {
        id,
        title,
        status: "active",
        doc_ref: null,
        autonomy: "propose",
        budget_usd: null,
        engine_key: engineKey,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        workspace_count: 0,
        total_spend_usd: 0,
      };
    }

    function stubProjects(entries: { id: string; title: string; engine_key?: string | null }[]) {
      server.use(
        http.get("/api/projects", () =>
          HttpResponse.json(entries.map((e) => projectEntry(e.id, e.title, e.engine_key ?? null))),
        ),
      );
    }

    function stubEngines(repos: { owner: string; repo: string }[]) {
      server.use(
        http.get("/api/engines", () =>
          HttpResponse.json({
            engines: repos.map((r) => ({ repo: r, posture: "autonomous" })),
          }),
        ),
      );
    }

    it("resolves engine_key from the bound project and never opens the repo chooser, even with more than one repo connected", async () => {
      let createCalls = 0;
      let capturedBody: { project_id?: string; engine_key?: string } | null = null;
      server.use(
        http.post("/api/build-chat/sessions", async ({ request }) => {
          createCalls += 1;
          capturedBody = (await request.json()) as { project_id?: string; engine_key?: string };
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
      );
      stubProjects([
        projectEntry("proj-alpha", "Alpha Project"),
        { id: "proj-beta", title: "Beta Project", engine_key: "acme-org/widgets" },
      ]);
      stubEngines([{ owner: "acme-org", repo: "widgets" }]);
      renderBuildChat("/build?project=proj-beta");
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
      expect(createCalls).toBe(1);
      expect(capturedBody).toEqual({ project_id: "proj-beta", engine_key: "acme-org/widgets" });
    });

    it("shows an inline notice and never creates a session when the bound project has no engine_key of its own", async () => {
      let createCalls = 0;
      server.use(
        http.post("/api/build-chat/sessions", () => {
          createCalls += 1;
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
      );
      stubProjects([projectEntry("proj-alpha", "Alpha Project")]);
      renderBuildChat("/build?project=proj-alpha");
      const notice = await screen.findByRole("status");
      expect(notice).toHaveTextContent("Repository unresolved");
      expect(notice).toHaveTextContent("no repository binding");
      expect(createCalls).toBe(0);
    });

    it("shows the same inline notice, and never creates a session, when the project id names no project in the list", async () => {
      let createCalls = 0;
      server.use(
        http.post("/api/build-chat/sessions", () => {
          createCalls += 1;
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
      );
      stubProjects([projectEntry("proj-other", "Other Project")]);
      renderBuildChat("/build?project=proj-alpha");
      const notice = await screen.findByRole("status");
      expect(notice).toHaveTextContent("Repository unresolved");
      expect(createCalls).toBe(0);
    });

    it("shows the same inline notice, and never creates a session, when the project lookup itself fails", async () => {
      let createCalls = 0;
      server.use(
        http.get("/api/projects", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
        http.post("/api/build-chat/sessions", () => {
          createCalls += 1;
          return HttpResponse.json({ session_id: SID }, { status: 201 });
        }),
      );
      renderBuildChat("/build?project=proj-alpha");
      const notice = await screen.findByRole("status");
      expect(notice).toHaveTextContent("Repository unresolved");
      expect(createCalls).toBe(0);
    });

    it("ignores a project-lookup resolution that lands after unmount", async () => {
      let resolve: ((value: Response) => void) | null = null;
      server.use(
        http.get(
          "/api/projects",
          () =>
            new Promise<Response>((res) => {
              resolve = res;
            }),
        ),
      );
      const { unmount } = renderBuildChat("/build?project=proj-alpha");
      await new Promise((r) => setTimeout(r, 5));
      unmount();
      resolve!(HttpResponse.json([projectEntry("proj-alpha", "Alpha Project")]));
      await new Promise((r) => setTimeout(r, 10));
    });

    it("ignores a project-lookup rejection that lands after unmount", async () => {
      let reject: ((reason?: unknown) => void) | null = null;
      server.use(
        http.get(
          "/api/projects",
          () =>
            new Promise<Response>((_res, rej) => {
              reject = rej;
            }),
        ),
      );
      const { unmount } = renderBuildChat("/build?project=proj-alpha");
      await new Promise((r) => setTimeout(r, 5));
      unmount();
      reject!(new Error("late project-lookup failure"));
      await new Promise((r) => setTimeout(r, 10));
    });
  });
});

describe("BuildChat — project count never gates the repo chooser", () => {
  function projectEntry(id: string, title: string) {
    return {
      id,
      title,
      status: "active",
      doc_ref: null,
      autonomy: "propose",
      budget_usd: null,
      engine_key: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      workspace_count: 0,
      total_spend_usd: 0,
    };
  }

  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => HttpResponse.json(defaultReadyProposal())),
    );
  });

  it("never shows any chooser and creates a session immediately with more than one project registered but only the default repo connected", async () => {
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([projectEntry("proj-alpha", "Alpha Project"), projectEntry("proj-beta", "Beta Project")]),
      ),
    );
    renderBuildChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled(), {
      timeout: 10_000,
    });
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  describe("Repo provisioning states", () => {
    it("shows an in-progress banner when repo_provision is pending", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(sessionResp({ repo_provision: "pending" })),
        ),
      );
      renderBuildChat();
      const banner = await screen.findByTestId("repo-provision-pending");
      expect(banner).toHaveAttribute("role", "status");
    });

    it("shows no banner when repo_provision is not_needed", async () => {
      renderBuildChat();
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-provision-pending")).not.toBeInTheDocument();
      expect(screen.queryByTestId("repo-provision-error")).not.toBeInTheDocument();
    });

    it("shows a retry-capable error banner and resends the last user message on retry", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(
            sessionResp({
              repo_provision: "error",
              messages: [
                { role: "user", content: "build this repo", ts: "2026-05-01T00:00:00Z" },
              ],
            }),
          ),
        ),
      );
      const sent: string[] = [];
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/messages`, async ({ request }) => {
          sent.push(((await request.json()) as { content: string }).content);
          return HttpResponse.json({ status: "accepted" }, { status: 202 });
        }),
      );
      const user = userEvent.setup();
      renderBuildChat();
      const banner = await screen.findByTestId("repo-provision-error");
      expect(banner).toHaveAttribute("role", "alert");
      await user.click(screen.getByTestId("repo-provision-retry"));
      await waitFor(() => expect(sent).toContain("build this repo"));
    });

    it("retry is a no-op when there is no prior user message", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(sessionResp({ repo_provision: "error" })),
        ),
      );
      const sent: string[] = [];
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/messages`, async ({ request }) => {
          sent.push(((await request.json()) as { content: string }).content);
          return HttpResponse.json({ status: "accepted" }, { status: 202 });
        }),
      );
      const user = userEvent.setup();
      renderBuildChat();
      await screen.findByTestId("repo-provision-error");
      await user.click(screen.getByTestId("repo-provision-retry"));
      await new Promise((r) => setTimeout(r, 50));
      expect(sent).toHaveLength(0);
    });
  });

  describe("Archived session redirect", () => {
    it("replace-navigates to the workspace prep-chat when the session GET redirects (303)", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, ({ request }) =>
          HttpResponse.redirect(
            new URL("/api/workspaces/archived-ws/prep-chat", request.url).toString(),
            303,
          ),
        ),
        http.get("/api/workspaces/archived-ws/prep-chat", () =>
          HttpResponse.json({
            session_id: SID,
            kind: "build",
            consultant_key: null,
            title: "Archived",
            created_at: "2026-05-01T00:00:00Z",
            messages: [],
          }),
        ),
      );
      renderBuildChat(`/build?s=${SID}`);
      expect(await screen.findByTestId("prep-chat-page")).toBeInTheDocument();
    });
  });
});
