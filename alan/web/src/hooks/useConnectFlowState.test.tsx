import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { SecretRecordResponse } from "../api";
import { server } from "../test/server";
import { credentialRecordName, hasTransportRecord, useConnectFlowState } from "./useConnectFlowState";

function secretRecord(name: string): SecretRecordResponse {
  return { name, fingerprint: "abc123", last_verified: null, expires_at: null, expiry_warned_at: null };
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
      { check: "mint_credentials", status: "fail", message: "m", remedy: "", remedy_url: "" },
    ],
    overall: "fail",
  };
}

describe("credentialRecordName", () => {
  it("matches alan.secrets_store.record_name(slug, 'transport')", () => {
    expect(credentialRecordName("acme", "widget")).toBe("acme/widget:transport");
  });
});

describe("hasTransportRecord", () => {
  it("is false for an empty secrets list", () => {
    expect(hasTransportRecord([])).toBe(false);
  });

  it("is false when only non-transport purposes are present", () => {
    expect(hasTransportRecord([secretRecord("acme/widget:reviewer")])).toBe(false);
  });

  it("is true when the default engine's own transport record is present", () => {
    expect(hasTransportRecord([secretRecord("DejvMatikar/alan:transport")])).toBe(true);
  });

  it("is true when a non-default engine's transport record is present among others", () => {
    expect(
      hasTransportRecord([secretRecord("acme/widget:reviewer"), secretRecord("acme/widget:transport")]),
    ).toBe(true);
  });
});

describe("useConnectFlowState — named states", () => {
  it("starts loading", () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    expect(result.current.state).toEqual({ status: "loading" });
  });

  it("ready: not registered, no credential yet, reachability unknown", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toEqual({
        status: "ready",
        registered: false,
        credentialPresent: false,
        reachability: "unknown",
        readiness: null,
      });
    });
  });

  it("ready: not registered, credential already created in Settings", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toEqual({
        status: "ready",
        registered: false,
        credentialPresent: true,
        reachability: "unknown",
        readiness: null,
      });
    });
  });

  it("ready: registered, credential present, reachability pass, and the full readiness payload is carried through", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () => HttpResponse.json(onboardingResponse("pass"))),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toEqual({
        status: "ready",
        registered: true,
        credentialPresent: true,
        reachability: "pass",
        readiness: onboardingResponse("pass"),
      });
    });
  });

  it("ready: registered, reachability fail", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () => HttpResponse.json(onboardingResponse("fail"))),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toMatchObject({ status: "ready", reachability: "fail" });
    });
    expect(result.current.state).toMatchObject({ readiness: onboardingResponse("fail") });
  });

  it("ready: registered, but the onboarding checklist carries no push_repository finding — falls back to 'unknown'", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json({
          state: { slug: "acme/widget" },
          checklist: [
            { check: "mint_credentials", status: "fail", message: "m", remedy: "", remedy_url: "" },
          ],
          overall: "fail",
        }),
      ),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toMatchObject({ status: "ready", reachability: "unknown" });
    });
  });

  it("ready: registered without a forge — 422 on onboarding maps to reachability 'no_forge', never a raw error", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json({ detail: "engine 'acme/widget' has no forge configured" }, { status: 422 }),
      ),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toEqual({
        status: "ready",
        registered: true,
        credentialPresent: false,
        reachability: "no_forge",
        readiness: null,
      });
    });
  });

  it("error: an unexpected failure reading engine registration (e.g. 500) surfaces as a typed error state", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toEqual({ status: "error", message: "500: boom" });
    });
  });

  it("error: an unexpected failure (e.g. 500) reading secrets surfaces as a typed error state", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toEqual({ status: "error", message: "500: boom" });
    });
  });

  it("error: an unexpected failure (e.g. 500, not 422) reading onboarding surfaces as a typed error state", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state).toEqual({ status: "error", message: "500: boom" });
    });
  });

  it("refetch() re-runs all three reads", async () => {
    let onboardingCalls = 0;
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "observe" })),
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () => {
        onboardingCalls += 1;
        return HttpResponse.json(onboardingResponse("pass"));
      }),
    );
    const { result } = renderHook(() => useConnectFlowState("acme", "widget"));
    await waitFor(() => {
      expect(result.current.state.status).toBe("ready");
    });
    expect(onboardingCalls).toBe(1);
    result.current.refetch();
    await waitFor(() => {
      expect(onboardingCalls).toBe(2);
    });
  });
});
