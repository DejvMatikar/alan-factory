export function errorMessageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function isErrorWithMessagePrefix(err: unknown, prefix: string): boolean {
  return err instanceof Error && err.message.startsWith(prefix);
}
