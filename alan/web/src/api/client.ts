import createClient, { type ClientOptions, type Middleware } from "openapi-fetch";

import { CONTROL_TOKEN_HEADER, readControlToken } from "../controlToken";
import type { paths } from "./schema";

const READ_ONLY_METHODS: ReadonlySet<string> = new Set(["GET", "HEAD", "OPTIONS"]);

export function isMutatingMethod(method: string): boolean {
  return !READ_ONLY_METHODS.has(method.toUpperCase());
}

function isSameOrigin(request: Request): boolean {
  return new URL(request.url).origin === globalThis.location.origin;
}

const controlTokenMiddleware: Middleware = {
  onRequest({ request }) {
    if (!isMutatingMethod(request.method) || !isSameOrigin(request)) return;
    const token = readControlToken();
    if (token) request.headers.set(CONTROL_TOKEN_HEADER, token);
  },
};

export function createApiClient(options?: ClientOptions) {
  const client = createClient<paths>(options);
  client.use(controlTokenMiddleware);
  return client;
}
