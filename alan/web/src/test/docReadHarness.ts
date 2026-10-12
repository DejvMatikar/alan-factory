import { act } from "@testing-library/react";

export interface DeferredFetch {
  readonly settled: Promise<void>;
  release: () => void;
}

export async function flushGc(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0);
    });
  });
}

export function deferredFetch(): DeferredFetch {
  let release: () => void = () => undefined;
  const settled = new Promise<void>((resolve) => {
    release = () => {
      resolve();
    };
  });
  return { settled, release };
}
