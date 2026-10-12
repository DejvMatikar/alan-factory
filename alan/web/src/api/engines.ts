import { createApiClient } from "./client";
import type { components } from "./schema";
import { unwrap } from "./unwrap";

const client = createApiClient({
  baseUrl: globalThis.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchEngineListing(): Promise<components["schemas"]["EngineListResponse"]> {
  return unwrap(await client.GET("/api/engines"));
}
