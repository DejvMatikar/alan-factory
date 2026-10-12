import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api";
import { server } from "../test/server";
import { fetchEngineListing } from "./engines";

const ENGINE = { repo: { owner: "acme", repo: "widget" }, posture: "observe", hidden: false };

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the call to reject");
}

describe("fetchEngineListing", () => {
  it("returns the engines together with the listed default key", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({ engines: [ENGINE], default_engine_key: "acme/widget" }),
      ),
    );
    await expect(fetchEngineListing()).resolves.toEqual({
      engines: [ENGINE],
      default_engine_key: "acme/widget",
    });
  });

  it("returns a listing with no default key when the field is absent", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.json({ engines: [ENGINE] })));
    const listing = await fetchEngineListing();
    expect(listing.engines).toEqual([ENGINE]);
    expect(listing.default_engine_key).toBeUndefined();
  });

  it("returns an explicit null default key untouched", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({ engines: [], default_engine_key: null }),
      ),
    );
    await expect(fetchEngineListing()).resolves.toEqual({ engines: [], default_engine_key: null });
  });

  it("rejects with an ApiError carrying the status and detail on a non-2xx response", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({ detail: "engine registry unavailable" }, { status: 503 }),
      ),
    );
    const error = await rejectionOf(fetchEngineListing());
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 503, detail: "engine registry unavailable" });
  });
});
