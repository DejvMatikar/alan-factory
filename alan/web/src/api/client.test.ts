import { beforeEach, describe, expect, it, vi } from "vitest";

import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "../controlToken";
import { createApiClient, isMutatingMethod } from "./client";

const TOKEN = "tok-123";
const TASK = { params: { path: { task_id: "task-1" } } };

type ApiClient = ReturnType<typeof createApiClient>;

interface LifecycleCase {
  label: string;
  method: string;
  path: string;
  call: (client: ApiClient) => Promise<unknown>;
}

const WORKSPACE_LIFECYCLE_MUTATIONS: LifecycleCase[] = [
  {
    label: "deleteWorkspace",
    method: "DELETE",
    path: "/api/workspaces/task-1",
    call: (client) => client.DELETE("/api/workspaces/{task_id}", TASK),
  },
  {
    label: "archiveWorkspace",
    method: "POST",
    path: "/api/workspaces/task-1/archive",
    call: (client) => client.POST("/api/workspaces/{task_id}/archive", TASK),
  },
  {
    label: "unarchiveWorkspace",
    method: "POST",
    path: "/api/workspaces/task-1/unarchive",
    call: (client) => client.POST("/api/workspaces/{task_id}/unarchive", TASK),
  },
  {
    label: "stopWorkspace",
    method: "POST",
    path: "/api/workspaces/task-1/stop",
    call: (client) => client.POST("/api/workspaces/{task_id}/stop", TASK),
  },
  {
    label: "resumeWorkspace",
    method: "POST",
    path: "/api/workspaces/task-1/resume",
    call: (client) => client.POST("/api/workspaces/{task_id}/resume", TASK),
  },
  {
    label: "clearStickyInstructions",
    method: "DELETE",
    path: "/api/workspaces/task-1/sticky-instructions",
    call: (client) => client.DELETE("/api/workspaces/{task_id}/sticky-instructions", TASK),
  },
];

function recordingClient(baseUrl: string = globalThis.location.origin) {
  const seen: Request[] = [];
  const fetch = vi.fn((request: Request) => {
    seen.push(request);
    return Promise.resolve(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
  });
  return { client: createApiClient({ baseUrl, fetch }), seen };
}

beforeEach(() => {
  clearControlToken();
});

describe("isMutatingMethod", () => {
  it.each(["POST", "PUT", "PATCH", "DELETE", "TRACE"])("treats %s as mutating", (method) => {
    expect(isMutatingMethod(method)).toBe(true);
  });

  it.each(["GET", "HEAD", "OPTIONS"])("treats %s as read-only", (method) => {
    expect(isMutatingMethod(method)).toBe(false);
  });

  it("is case-insensitive", () => {
    expect(isMutatingMethod("delete")).toBe(true);
    expect(isMutatingMethod("get")).toBe(false);
  });

  it("treats a method it has never heard of as mutating", () => {
    expect(isMutatingMethod("PURGE")).toBe(true);
  });
});

describe("createApiClient control token", () => {
  it.each(WORKSPACE_LIFECYCLE_MUTATIONS)(
    "attaches the control token to $label",
    async ({ method, path, call }) => {
      writeControlToken(TOKEN);
      const { client, seen } = recordingClient();
      await call(client);
      expect(seen).toHaveLength(1);
      expect(seen[0]?.method).toBe(method);
      expect(new URL(seen[0]?.url ?? "").pathname).toBe(path);
      expect(seen[0]?.headers.get(CONTROL_TOKEN_HEADER)).toBe(TOKEN);
    },
  );

  it("attaches the token to a mutating request that carries a JSON body", async () => {
    writeControlToken(TOKEN);
    const { client, seen } = recordingClient();
    await client.POST("/api/workspaces/{task_id}/archive", TASK);
    await client.PUT("/api/workspaces/{task_id}/note", {
      ...TASK,
      body: { note: "hello" },
    });
    expect(seen.map((request) => request.headers.get(CONTROL_TOKEN_HEADER))).toEqual([
      TOKEN,
      TOKEN,
    ]);
  });

  it("sends no token header on a read-only request even when one is stored", async () => {
    writeControlToken(TOKEN);
    const { client, seen } = recordingClient();
    await client.GET("/api/health");
    expect(seen).toHaveLength(1);
    expect(seen[0]?.method).toBe("GET");
    expect(seen[0]?.headers.has(CONTROL_TOKEN_HEADER)).toBe(false);
  });

  it("sends no token header on a mutating request when none is stored", async () => {
    const { client, seen } = recordingClient();
    await client.POST("/api/workspaces/{task_id}/stop", TASK);
    expect(seen[0]?.headers.has(CONTROL_TOKEN_HEADER)).toBe(false);
  });

  it("never sends the token to a different origin than the page", async () => {
    writeControlToken(TOKEN);
    const { client, seen } = recordingClient("http://remote.example");
    await client.POST("/api/workspaces/{task_id}/stop", TASK);
    expect(new URL(seen[0]?.url ?? "").origin).toBe("http://remote.example");
    expect(seen[0]?.headers.has(CONTROL_TOKEN_HEADER)).toBe(false);
  });

  it("reads the stored token at request time, not at client creation", async () => {
    const { client, seen } = recordingClient();
    writeControlToken("late-token");
    await client.POST("/api/workspaces/{task_id}/resume", TASK);
    expect(seen[0]?.headers.get(CONTROL_TOKEN_HEADER)).toBe("late-token");
  });
});
