import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import ConnectWizard from "./ConnectWizard";

function secretsResponse(names: string[]) {
  return {
    secrets: names.map((name) => ({
      name,
      fingerprint: "abc123",
      last_verified: null,
      expires_at: null,
      expiry_warned_at: null,
    })),
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/connect" element={<ConnectWizard />} />
        <Route path="/connect/:owner/:repo" element={<ConnectWizard />} />
        <Route path="/connect/:owner/:repo/:step" element={<ConnectWizard />} />
      </Routes>
    </MemoryRouter>,
  );
}

function onboardingResponse(overrides: {
  scaffold_pr_number?: number | null;
  scaffold_pr_url?: string | null;
  protection_deferred?: unknown[];
  protection_applied_keys?: string[];
  merged?: boolean;
} = {}) {
  return {
    state: {
      slug: "acme/widget", stack: "python",
      scaffold_pr_number: overrides.scaffold_pr_number ?? null,
      scaffold_pr_url: overrides.scaffold_pr_url ?? null,
      authoritative_checks: [], protection_applied_keys: overrides.protection_applied_keys ?? [],
      protection_deferred: overrides.protection_deferred ?? [],
    },
    checklist: [
      {
        check: "review_and_merge_scaffold_pr",
        status: overrides.merged ? "pass" : "fail",
        message: overrides.merged ? "merged" : "scaffold has not been run yet",
        remedy: "", remedy_url: "",
      },
    ],
    overall: "fail",
  };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname}</div>;
}

describe("ConnectWizard", () => {
  it("renders the title, the rail, and the fresh-start identity form at /connect", () => {
    renderAt("/connect");
    expect(screen.getByRole("heading", { name: "Connect a repository" })).toBeInTheDocument();
    expect(screen.getByTestId("wizard-rail")).toBeInTheDocument();
    expect(screen.getByTestId("connect-identity-form")).toBeInTheDocument();
  });

  it("submitting the identity form navigates to the per-repo resumable route", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
    );
    renderAt("/connect");
    const user = userEvent.setup();
    await user.type(screen.getByTestId("connect-repo-url-input"), "acme/widget");
    await user.click(screen.getByTestId("connect-identity-continue"));
    await waitFor(() => {
      expect(screen.getByTestId("step1-identity-detail")).toHaveTextContent("acme/widget");
    });
  });

  it("reads identity directly from the URL on the resumable route, with no client-stored progress", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" }),
      ),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engines/acme/widget/onboarding", () => HttpResponse.json(onboardingResponse())),
    );
    renderAt("/connect/acme/widget");
    expect(screen.queryByTestId("connect-identity-form")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("step1-credential-status")).toHaveTextContent("fail");
    });
  });

  it("re-entry with step 1 complete but configure not yet started lands on step 2 (configure)", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" }),
      ),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () => HttpResponse.json(onboardingResponse())),
      http.post("/api/engines/acme/widget/config-proposal", () => new Promise(() => undefined)),
    );
    renderAt("/connect/acme/widget");
    await waitFor(() => {
      expect(screen.getByTestId("connect-step2-configure")).toBeInTheDocument();
    });
    expect(screen.getByTestId("wizard-rail-step-configure")).toHaveAttribute("aria-current", "step");
    expect(screen.queryByTestId("connect-step3-dispatchable")).not.toBeInTheDocument();
  });

  it("re-entry once configure has started (a scaffold PR is open) lands on step 3 (make dispatchable) and hoists its blocking row", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" }),
      ),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json(
          onboardingResponse({
            scaffold_pr_number: 1,
            scaffold_pr_url: "https://github.com/acme/widget/pull/1",
          }),
        ),
      ),
    );
    renderAt("/connect/acme/widget");
    await waitFor(() => {
      expect(screen.getByTestId("step3-scaffold-status")).toHaveTextContent("waiting on you");
    });
    expect(screen.getByTestId("wizard-rail-step-dispatchable")).toHaveAttribute("aria-current", "step");
  });

  it("a deep link to configure (now built) renders step 2 directly, even before step 1 is complete", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/engines/acme/widget/config-proposal", () =>
        HttpResponse.json(
          {
            detail: {
              check: "config_proposal",
              status: "fail",
              message:
                "engine 'acme/widget' is not registered with a forge configured — cannot provision a " +
                "read-only checkout of it to inspect",
              remedy: "seed the engine configuration from a stack preset manually in the engine configuration editor",
              remedy_url: "",
            },
          },
          { status: 422 },
        ),
      ),
    );
    renderAt("/connect/acme/widget/configure");
    await waitFor(() => {
      expect(screen.getByTestId("connect-step2-configure")).toBeInTheDocument();
    });
    expect(screen.getByTestId("wizard-rail-step-configure")).toHaveAttribute("aria-current", "step");
    expect(screen.queryByTestId("connect-step1-checklist")).not.toBeInTheDocument();
    expect(await screen.findByTestId("step2-error")).toBeInTheDocument();
  });

  it("a deep link to /posture renders step 4 even though step 1 isn't complete yet", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/engines/acme/widget/preflight", () =>
        HttpResponse.json({
          slug: "acme/widget", checked_at: "2026-07-29T00:00:00Z", overall: "unknown", findings: [],
        }),
      ),
      http.get("/api/workspaces", () => HttpResponse.json([])),
    );
    renderAt("/connect/acme/widget/posture");
    expect(await screen.findByTestId("connect-step4-posture")).toBeInTheDocument();
    expect(screen.getByTestId("wizard-rail-step-posture")).toHaveAttribute("aria-current", "step");
  });

  it("a dispatchable-complete state derives to posture — the only remaining built step", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" }),
      ),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json(
          onboardingResponse({
            scaffold_pr_number: 7,
            merged: true,
            protection_applied_keys: ["squash_merge_enabled"],
          }),
        ),
      ),
      http.post("/api/engines/acme/widget/preflight", () =>
        HttpResponse.json({
          slug: "acme/widget", checked_at: "2026-07-29T00:00:00Z", overall: "unknown", findings: [],
        }),
      ),
      http.get("/api/workspaces", () => HttpResponse.json([])),
    );
    renderAt("/connect/acme/widget");
    expect(await screen.findByTestId("connect-step4-posture")).toBeInTheDocument();
    expect(screen.getByTestId("wizard-rail-step-posture")).toHaveAttribute("aria-current", "step");
  });

  it("an invalid step deep link bounces back to the canonical URL even when the onboarding read errors", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
    );
    render(
      <MemoryRouter initialEntries={["/connect/acme/widget/posture"]}>
        <LocationProbe />
        <Routes>
          <Route path="/connect/:owner/:repo" element={<ConnectWizard />} />
          <Route path="/connect/:owner/:repo/:step" element={<ConnectWizard />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/connect/acme/widget");
    });
  });

  it("an unknown step deep link bounces back to the canonical bare URL — the only step key left that isn't built", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
    );
    render(
      <MemoryRouter initialEntries={["/connect/acme/widget/bogus-step"]}>
        <LocationProbe />
        <Routes>
          <Route path="/connect/:owner/:repo" element={<ConnectWizard />} />
          <Route path="/connect/:owner/:repo/:step" element={<ConnectWizard />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent(/^\/connect\/acme\/widget$/);
    });
  });

  it("a background refetch while step 3 is open keeps the step mounted and does not duplicate an in-flight protect() call", async () => {
    let protectCalls = 0;
    let resolveProtect!: () => void;
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" }),
      ),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json(onboardingResponse({ scaffold_pr_number: 7, merged: true })),
      ),
      http.post("/api/engines/acme/widget/protect", () => {
        protectCalls += 1;
        return new Promise((resolve) => {
          resolveProtect = () =>
            resolve(HttpResponse.json(onboardingResponse({ scaffold_pr_number: 7, merged: true }).state));
        });
      }),
    );
    renderAt("/connect/acme/widget");

    await waitFor(() => {
      expect(protectCalls).toBe(1);
    });
    expect(screen.getByTestId("connect-step3-dispatchable")).toBeInTheDocument();

    act(() => {
      Object.defineProperty(document, "hidden", { value: true, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    act(() => {
      Object.defineProperty(document, "hidden", { value: false, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(screen.getByTestId("connect-step3-dispatchable")).toBeInTheDocument();
    expect(screen.queryByTestId("connect-identity-form")).not.toBeInTheDocument();
    expect(protectCalls).toBe(1);

    resolveProtect();
    await waitFor(() => {
      expect(screen.getByTestId("connect-step3-dispatchable")).toBeInTheDocument();
    });
  });

  it("a valid built-step override renders that step even though step 1 isn't complete yet", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
    );
    renderAt("/connect/acme/widget/dispatchable");
    await waitFor(() => {
      expect(screen.getByTestId("step3-no-readiness")).toBeInTheDocument();
    });
    expect(screen.getByTestId("wizard-rail-step-dispatchable")).toHaveAttribute("aria-current", "step");
  });
});
