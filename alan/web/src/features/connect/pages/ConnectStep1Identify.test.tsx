import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../../../test/server";
import ConnectStep1Identify from "./ConnectStep1Identify";

function engineNotFound() {
  return HttpResponse.json({ detail: "not found" }, { status: 404 });
}

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

function onboardingResponse(pushRepositoryStatus: "pass" | "fail" | "unknown") {
  return {
    state: { slug: "acme/widget" },
    checklist: [
      { check: "push_repository", status: pushRepositoryStatus, message: "m", remedy: "", remedy_url: "" },
    ],
    overall: "fail",
  };
}

function presetConfigResponse() {
  return {
    repo: { owner: "acme", repo: "widget" },
    code_quality: null,
    forge: {
      repo: { owner: "acme", repo: "widget" },
      credential_env_name: "ACME_WIDGET_GH_TOKEN",
      authoritative_checks: [],
    },
    ci: null,
    roles: {},
    gate_commands: [],
    warmup_commands: [],
    base_tools: [],
    team_tools: {},
    transition_graph: {},
    flow_policy: null,
    posture: "observe",
    pool_size: null,
    preset: "python",
    prompt_overrides: {},
  };
}

describe("ConnectStep1Identify — fresh-start identity form", () => {
  it("disables Continue until a resolvable repository is entered, then calls onIdentityKnown", async () => {
    const onIdentityKnown = vi.fn();
    render(<ConnectStep1Identify identity={null} onIdentityKnown={onIdentityKnown} />);
    expect(screen.getByTestId("connect-identity-continue")).toBeDisabled();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("connect-repo-url-input"), "acme/widget");
    expect(screen.getByTestId("connect-identity-continue")).not.toBeDisabled();
    await user.click(screen.getByTestId("connect-identity-continue"));
    expect(onIdentityKnown).toHaveBeenCalledWith("acme", "widget");
  });

  it("echoes the resolved owner/repo as a polite live region before the user commits", async () => {
    render(<ConnectStep1Identify identity={null} onIdentityKnown={vi.fn()} />);
    const user = userEvent.setup();
    expect(screen.queryByTestId("connect-identity-resolved")).not.toBeInTheDocument();
    await user.type(
      screen.getByTestId("connect-repo-url-input"),
      "https://github.com/acme/widget",
    );
    const resolved = screen.getByTestId("connect-identity-resolved");
    expect(resolved).toHaveTextContent("acme/widget");
    expect(resolved).toHaveAttribute("role", "status");
    expect(resolved).toHaveAttribute("aria-live", "polite");
  });

  it("accepts a pasted repository URL, not just a bare slug", async () => {
    const onIdentityKnown = vi.fn();
    render(<ConnectStep1Identify identity={null} onIdentityKnown={onIdentityKnown} />);
    const user = userEvent.setup();
    await user.type(
      screen.getByTestId("connect-repo-url-input"),
      "https://github.com/acme/widget.git",
    );
    await user.click(screen.getByTestId("connect-identity-continue"));
    expect(onIdentityKnown).toHaveBeenCalledWith("acme", "widget");
  });

  it("does not call onIdentityKnown when the trimmed field is blank (whitespace-only input)", async () => {
    const onIdentityKnown = vi.fn();
    render(<ConnectStep1Identify identity={null} onIdentityKnown={onIdentityKnown} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("connect-repo-url-input"), "  ");
    screen.getByTestId("connect-identity-form").dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
    expect(onIdentityKnown).not.toHaveBeenCalled();
  });

  it("rejects a non-GitHub host inline as the user types, with Continue disabled — no submit needed", async () => {
    const onIdentityKnown = vi.fn();
    render(<ConnectStep1Identify identity={null} onIdentityKnown={onIdentityKnown} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("connect-repo-url-input"), "https://gitlab.com/acme/widget");
    expect(screen.getByTestId("connect-identity-error")).toHaveTextContent(
      "Only github.com repositories are supported.",
    );
    expect(screen.getByTestId("connect-identity-continue")).toBeDisabled();
    expect(onIdentityKnown).not.toHaveBeenCalled();
  });

  it("rejects unparsable text inline as the user types, with Continue disabled — no submit needed", async () => {
    const onIdentityKnown = vi.fn();
    render(<ConnectStep1Identify identity={null} onIdentityKnown={onIdentityKnown} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("connect-repo-url-input"), "not a url at all");
    expect(screen.getByTestId("connect-identity-error")).toHaveTextContent(
      "Enter a GitHub repository URL or an owner/repo slug.",
    );
    expect(screen.getByTestId("connect-identity-continue")).toBeDisabled();
    expect(onIdentityKnown).not.toHaveBeenCalled();
  });

  it("clears a stale inline error as soon as the field is edited into a resolvable value", async () => {
    const onIdentityKnown = vi.fn();
    render(<ConnectStep1Identify identity={null} onIdentityKnown={onIdentityKnown} />);
    const user = userEvent.setup();
    const input = screen.getByTestId("connect-repo-url-input");
    await user.type(input, "https://gitlab.com/acme/widget");
    expect(screen.getByTestId("connect-identity-error")).toBeInTheDocument();
    await user.clear(input);
    await user.type(input, "acme/widget");
    expect(screen.queryByTestId("connect-identity-error")).not.toBeInTheDocument();
    expect(screen.getByTestId("connect-identity-continue")).not.toBeDisabled();
  });

  it("rejects owner/repo outside the URL-safe segment charset inline, with Continue disabled", async () => {
    const onIdentityKnown = vi.fn();
    render(<ConnectStep1Identify identity={null} onIdentityKnown={onIdentityKnown} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("connect-repo-url-input"), "acme!bad/widget");
    expect(screen.getByTestId("connect-identity-error")).toHaveTextContent(
      "Owner and repository may only contain letters, numbers",
    );
    expect(screen.getByTestId("connect-identity-continue")).toBeDisabled();
    expect(screen.queryByTestId("connect-identity-resolved")).not.toBeInTheDocument();
    expect(onIdentityKnown).not.toHaveBeenCalled();
  });
});

describe("ConnectStep1Identify — resumable checklist", () => {
  it("renders the forced order identity -> credential -> reachability, plus the proposal row, for a blank slate", async () => {
    server.use(
      http.get("/api/engines/acme/widget", engineNotFound),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
    );
    render(<ConnectStep1Identify identity={{ owner: "acme", repo: "widget" }} onIdentityKnown={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("connect-step1-checklist")).toBeInTheDocument();
    });
    expect(screen.getByTestId("step1-identity-status")).toHaveTextContent("pass");
    expect(screen.getByTestId("step1-identity-detail")).toHaveTextContent("acme/widget");
    expect(screen.getByTestId("step1-credential-status")).toHaveTextContent("fail");
    expect(screen.getByTestId("step1-credential-remedy")).toHaveTextContent("Settings");
    expect(screen.getByTestId("step1-reachability-status")).toHaveTextContent("unknown");
    expect(screen.getByTestId("step1-proposal-status")).toHaveTextContent("not yet checked");
    expect(screen.getByTestId("step1-footer-caption")).toHaveTextContent(
      "Registers acme/widget with alan and starts a read-only scan of its stack",
    );
    expect(screen.getByTestId("step1-footer-primary")).not.toBeDisabled();
  });

  it("shows 'already registered', disables the action, when the engine already exists", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" }),
      ),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () => HttpResponse.json(onboardingResponse("pass"))),
    );
    render(<ConnectStep1Identify identity={{ owner: "acme", repo: "widget" }} onIdentityKnown={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("step1-reachability-status")).toHaveTextContent("pass");
    });
    expect(screen.getByTestId("step1-footer-caption")).toHaveTextContent("already registered");
    expect(screen.getByTestId("step1-footer-primary")).toBeDisabled();
  });

  it("clicking Connect repository runs the 5-call action and turns the checklist green on success", async () => {
    let engineRegistered = false;
    server.use(
      http.get("/api/engines/acme/widget", () => (engineRegistered ? HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" }) : engineNotFound())),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () => HttpResponse.json(onboardingResponse("pass"))),
      http.get("/api/engine-presets/python/config", () => HttpResponse.json(presetConfigResponse())),
      http.post("/api/engines", () => {
        engineRegistered = true;
        return HttpResponse.json(presetConfigResponse(), { status: 201 });
      }),
      http.post("/api/engines/acme/widget/config-proposal", () =>
        HttpResponse.json({
          slug: "acme/widget",
          base_preset: "python",
          detected_stacks: ["python"],
          additional_stacks: [],
          proposed_config: presetConfigResponse(),
          diff: [],
          validation: { check: "config_proposal", status: "pass", message: "ok", remedy: "", remedy_url: "" },
        }),
      ),
    );
    render(<ConnectStep1Identify identity={{ owner: "acme", repo: "widget" }} onIdentityKnown={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("step1-credential-status")).toHaveTextContent("pass");
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step1-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("step1-proposal-status")).toHaveTextContent("pass");
    });
    expect(screen.queryByTestId("step1-mutation-error")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("step1-footer-primary")).toBeDisabled();
    });
  });

  it("a register-stage failure (missing credential) blocks with a typed notice and leaves the proposal row pending", async () => {
    server.use(
      http.get("/api/engines/acme/widget", engineNotFound),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engine-presets/python/config", () => HttpResponse.json(presetConfigResponse())),
      http.post("/api/engines", () =>
        HttpResponse.json({ detail: "no secret record for 'acme/widget:transport'" }, { status: 422 }),
      ),
    );
    render(<ConnectStep1Identify identity={{ owner: "acme", repo: "widget" }} onIdentityKnown={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("step1-footer-primary")).not.toBeDisabled();
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step1-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("step1-mutation-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("step1-mutation-error-message")).toHaveTextContent("Credentials");
    expect(screen.getByTestId("step1-mutation-error-detail")).toHaveTextContent(
      "acme/widget:transport",
    );
    expect(screen.getByTestId("step1-proposal-status")).toHaveTextContent("not yet checked");
  });

  it("a failure fetching the preset baseline itself is classified as a register-stage failure", async () => {
    server.use(
      http.get("/api/engines/acme/widget", engineNotFound),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engine-presets/python/config", () =>
        HttpResponse.json({ detail: "preset not found" }, { status: 404 }),
      ),
    );
    render(<ConnectStep1Identify identity={{ owner: "acme", repo: "widget" }} onIdentityKnown={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("step1-footer-primary")).not.toBeDisabled();
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step1-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("step1-mutation-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("step1-proposal-status")).toHaveTextContent("not yet checked");
  });

  it("a propose-stage failure marks the stack-scan row failed and shows the proposal_failed notice", async () => {
    server.use(
      http.get("/api/engines/acme/widget", engineNotFound),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engine-presets/python/config", () => HttpResponse.json(presetConfigResponse())),
      http.post("/api/engines", () => HttpResponse.json(presetConfigResponse(), { status: 201 })),
      http.post("/api/engines/acme/widget/config-proposal", () =>
        HttpResponse.json(
          {
            detail: {
              check: "config_proposal",
              status: "fail",
              message: "aborted",
              remedy: "seed manually",
            },
          },
          { status: 422 },
        ),
      ),
    );
    render(<ConnectStep1Identify identity={{ owner: "acme", repo: "widget" }} onIdentityKnown={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("step1-footer-primary")).not.toBeDisabled();
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step1-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("step1-proposal-status")).toHaveTextContent("fail");
    });
    expect(screen.getByTestId("step1-mutation-error-message")).toHaveTextContent(
      "automatic stack scan couldn't complete",
    );
  });

  it("surfaces an unexpected read failure (e.g. 500) as a typed error, not a blank screen", async () => {
    server.use(
      http.get("/api/engines/acme/widget", engineNotFound),
      http.get("/api/secrets", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    render(<ConnectStep1Identify identity={{ owner: "acme", repo: "widget" }} onIdentityKnown={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("step1-error")).toHaveTextContent("500: boom");
    });
  });
});
