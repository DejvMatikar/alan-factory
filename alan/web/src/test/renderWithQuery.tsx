import { render, renderHook } from "@testing-library/react";
import type {
  RenderHookOptions,
  RenderHookResult,
  RenderOptions,
  RenderResult,
} from "@testing-library/react";
import type { ReactElement } from "react";

import { QueryProvider } from "../api/QueryProvider";

export function renderWithQuery(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
): RenderResult {
  return render(ui, { ...options, wrapper: QueryProvider });
}

export function renderHookWithQuery<Result, Props>(
  callback: (initialProps: Props) => Result,
  options?: Omit<RenderHookOptions<Props>, "wrapper">,
): RenderHookResult<Result, Props> {
  return renderHook(callback, { ...options, wrapper: QueryProvider });
}
