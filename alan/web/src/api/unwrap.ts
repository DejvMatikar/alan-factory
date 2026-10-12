import { ApiError } from "../api";

function nestedDetail(detail: unknown): string | null {
  if (typeof detail !== "object" || detail === null) return null;
  const inner = (detail as { detail?: unknown }).detail;
  return typeof inner === "string" ? inner : null;
}

function errorDetail(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  return typeof detail === "string" ? detail : (nestedDetail(detail) ?? fallback);
}

interface ClientResult<T> {
  data?: T | undefined;
  error?: unknown;
  response: Response;
}

export function unwrap<T>({ data, error, response }: ClientResult<T>): T {
  if (!response.ok) {
    throw new ApiError(response.status, errorDetail(error, response.statusText));
  }
  return data as T;
}
