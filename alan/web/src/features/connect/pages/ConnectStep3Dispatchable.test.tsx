import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { EnginePreflightFinding, OnboardingReadiness, OnboardingState } from "../../../api";
import { server } from "../../../test/server";
import type { ConnectFlowState } from "../../../hooks/useConnectFlowState";
import ConnectStep3Dispatchable from "./ConnectStep3Dispatchable";

const ENGINE_KEY = "acme/widget";

function finding(overrides: Partial<EnginePreflightFinding> = {}): EnginePreflightFinding {
  return { check: "push_repository", status: "pass", message: "m", remedy: "", remedy_url: "", ...overrides };
}

function state(overrides: Partial<OnboardingState> = {}): OnboardingState {
  return {
    slug: ENGINE_KEY, stack: "python", scaffold_pr_number: null, scaffold_pr_url: null,
    authoritative_checks: [], protection_applied_keys: [], protection_deferred: [], ...overrides,
  };
}

function readiness(
  checklist: EnginePreflightFinding[],
  stateOverrides: Partial<OnboardingState> = {},
): OnboardingReadiness {
  return { state: state(stateOverrides), checklist, overall: "fail" };
}

function readyState(
  readinessValue: OnboardingReadiness | null,
  credentialPresent = true,
): ConnectFlowState {
  return {
    status: "ready", registered: true, credentialPresent, reachability: "pass",
    readiness: readinessValue,
  };
}

function renderStep3(state: ConnectFlowState, refetch: () => void = vi.fn()) {
  return render(
    <MemoryRouter>
      <ConnectStep3Dispatchable engineKey={ENGINE_KEY} state={state} refetch={refetch} />
    </MemoryRouter>,
  );
}

describe("ConnectStep3Dispatchable — degenerate states", () => {
  it("shows a loading state", () => {
    renderStep3({ status: "loading" });
    expect(screen.getByTestId("step3-loading")).toBeInTheDocument();
  });

  it("surfaces a typed error", () => {
    renderStep3({ status: "error", message: "500: boom" });
    expect(screen.getByTestId("step3-error")).toHaveTextContent("500: boom");
  });

  it("tells the user to finish step 1 first when there is no readiness yet (engine not registered)", () => {
    renderStep3(readyState(null));
    expect(screen.getByTestId("step3-no-readiness")).toHaveTextContent("Finish step 1");
  });
});

describe("ConnectStep3Dispatchable — first entry (unstarted)", () => {
  it("renders every row with zero danger chips and the not-started scaffold row", () => {
    const r = readiness([
      finding({ check: "push_repository", status: "unknown", message: "cannot verify — no transport credential yet" }),
      finding({ check: "mint_credentials", status: "fail", message: "no transport credential" }),
      finding({ check: "grant_reviewer_collaborator_access", status: "unknown", message: "cannot verify" }),
      finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "scaffold has not been run yet" }),
    ]);
    renderStep3(readyState(r, false));
    expect(screen.getByTestId("step3-scaffold-status")).toHaveTextContent("not started");
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(screen.getByTestId("step3-footer-primary")).not.toBeDisabled();
    expect(screen.getByTestId("step3-planning-link")).toHaveAttribute("href", "/build");
  });

  it("hoists the reviewer-credential-missing finding as the first, blocking row", () => {
    const r = readiness([
      finding({ check: "push_repository", status: "pass", message: "reachable" }),
      {
        check: "mint_credentials", status: "fail", message: "no reviewer credential configured",
        remedy: "write a reviewer secret for this repo via the secrets store, distinct from the transport credential",
        remedy_url: "",
      },
      finding({ check: "grant_reviewer_collaborator_access", status: "unknown", message: "cannot verify" }),
      finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "scaffold has not been run yet" }),
    ]);
    renderStep3(readyState(r, true));
    expect(screen.getByTestId("step3-mint_credentials")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("step3-mint_credentials-remedy")).toHaveTextContent("write a reviewer secret");
    expect(screen.getByTestId("step3-mint_credentials-remedy")).toHaveTextContent("Settings");
  });
});

describe("ConnectStep3Dispatchable — scaffold action", () => {
  it("clicking Open scaffold PR calls scaffoldEngine and triggers a refetch", async () => {
    server.use(
      http.post("/api/engines/acme/widget/scaffold", () =>
        HttpResponse.json(state({ scaffold_pr_number: 7, scaffold_pr_url: "https://github.com/acme/widget/pull/7" })),
      ),
    );
    const refetch = vi.fn();
    const r = readiness([finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "scaffold has not been run yet" })]);
    renderStep3(readyState(r, false), refetch);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step3-footer-primary"));
    await waitFor(() => {
      expect(refetch).toHaveBeenCalled();
    });
  });

  it("surfaces a scaffold failure inline without blanking the checklist", async () => {
    server.use(
      http.post("/api/engines/acme/widget/scaffold", () =>
        HttpResponse.json({ detail: "no transport credential for 'acme/widget'" }, { status: 422 }),
      ),
    );
    const r = readiness([finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "scaffold has not been run yet" })]);
    renderStep3(readyState(r, false));
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step3-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("step3-scaffold-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("connect-step3-dispatchable")).toBeInTheDocument();
  });

  it("surfaces the forge's own rejection reason via the typed notice, never a raw internal enum name", async () => {
    server.use(
      http.post("/api/engines/acme/widget/scaffold", () =>
        HttpResponse.json(
          {
            detail:
              "git push failed: refusing to allow a Personal Access Token to create or update " +
              "workflow `.github/workflows/ci.yml` without the `workflow` scope",
          },
          { status: 422 },
        ),
      ),
    );
    const r = readiness([finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "scaffold has not been run yet" })]);
    renderStep3(readyState(r, false));
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step3-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("step3-scaffold-error-message")).toHaveTextContent(
        "scaffold pull request couldn't be opened",
      );
    });
    expect(screen.getByTestId("step3-scaffold-error-detail")).toHaveTextContent(
      "without the `workflow` scope",
    );
    expect(screen.getByTestId("step3-scaffold-error").textContent).not.toContain("PUSH_FAILED");
  });

  it("shows the waiting state with a PR link, and the already-open footer caption", () => {
    const r = readiness(
      [finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "PR #7 not yet merged (state=OPEN)" })],
      { scaffold_pr_number: 7, scaffold_pr_url: "https://github.com/acme/widget/pull/7" },
    );
    renderStep3(readyState(r, true));
    expect(screen.getByTestId("step3-scaffold-status")).toHaveTextContent("waiting on you");
    expect(screen.getByTestId("step3-scaffold-link")).toHaveAttribute(
      "href",
      "https://github.com/acme/widget/pull/7",
    );
    expect(screen.getByTestId("step3-footer-caption")).toHaveTextContent("already open");
  });
});

describe("ConnectStep3Dispatchable — protection rows and the poll caption", () => {
  it("renders deferred protection rows with a working settings link, and the merged+deferred poll caption", () => {
    const deferredState = state({
      scaffold_pr_number: 7,
      protection_deferred: [
        {
          check: "required_checks", status: "fail", message: "does not satisfy: required checks",
          remedy: "add the check", remedy_url: "https://github.com/acme/widget/settings/branches",
        },
      ],
    });
    server.use(
      http.post("/api/engines/acme/widget/protect", () => HttpResponse.json(deferredState)),
    );
    const r: OnboardingReadiness = {
      state: deferredState,
      checklist: [
        { check: "review_and_merge_scaffold_pr", status: "pass", message: "PR #7 merged", remedy: "", remedy_url: "" },
      ],
      overall: "fail",
    };
    renderStep3(readyState(r, true));
    expect(screen.getByTestId("step3-protection-required_checks")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("step3-protection-required_checks-link")).toHaveAttribute(
      "href",
      "https://github.com/acme/widget/settings/branches",
    );
    expect(screen.getByTestId("step3-poll-caption")).toHaveAttribute("aria-live", "polite");
  });
});
