import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { ApiError, api } from "./api";
import { server } from "./test/server";

describe("api.getHealthDigest", () => {
  it("GETs /api/health/digest and returns the parsed response", async () => {
    let method = "";
    server.use(
      http.get("/api/health/digest", ({ request }) => {
        method = request.method;
        return HttpResponse.json({
          recovery: { current_count: 4, previous_count: 2, direction: "up" },
          residue: [{ fingerprint: "abc123", title: "Unclassified spike" }],
          digest_overdue: false,
        });
      }),
    );
    const resp = await api.getHealthDigest();
    expect(method).toBe("GET");
    expect(resp).toEqual({
      recovery: { current_count: 4, previous_count: 2, direction: "up" },
      residue: [{ fingerprint: "abc123", title: "Unclassified spike" }],
      digest_overdue: false,
    });
  });

  it("returns a null recovery and a null residue when no digest has ever been produced", async () => {
    server.use(
      http.get("/api/health/digest", () =>
        HttpResponse.json({ recovery: null, residue: null, digest_overdue: true }),
      ),
    );
    const resp = await api.getHealthDigest();
    expect(resp.recovery).toBeNull();
    expect(resp.residue).toBeNull();
    expect(resp.digest_overdue).toBe(true);
  });

  it("distinguishes an empty residue window from a never-measured one", async () => {
    server.use(
      http.get("/api/health/digest", () =>
        HttpResponse.json({ recovery: null, residue: [], digest_overdue: false }),
      ),
    );
    const resp = await api.getHealthDigest();
    expect(resp.residue).toEqual([]);
  });

  it("rejects with an ApiError on a non-2xx response", async () => {
    server.use(
      http.get("/api/health/digest", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    let caught: ApiError | null = null;
    try {
      await api.getHealthDigest();
    } catch (err) {
      if (err instanceof ApiError) caught = err;
    }
    expect(caught?.status).toBe(500);
    expect(caught?.detail).toBe("boom");
  });
});
