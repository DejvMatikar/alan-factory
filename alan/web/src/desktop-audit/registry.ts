export type DesktopMaxState =
  | { readonly kind: "seeded"; readonly uncovered: readonly string[] }
  | { readonly kind: "none"; readonly noMaxStateReason: string; readonly uncovered: readonly string[] };

export interface DesktopStateRegistryEntry {
  readonly screen: string;
  readonly maxState: DesktopMaxState;
}

export type DesktopStateRegistry = readonly DesktopStateRegistryEntry[];

export interface RegistryValidationResult {
  readonly valid: boolean;
  readonly errors: readonly string[];
}

// "@" is the screen@state baseline-key separator (desktop-audit/baseline-compare.ts) —
// a screen name containing it would let two different (screen, state) pairs collide
// on one baseline key, silently merging their tolerated counts.
const SCREEN_NAME_PATTERN = /^[a-z0-9-]+$/;

function validateScreenNames(screenNames: readonly string[]): string[] {
  return screenNames
    .filter((name) => !SCREEN_NAME_PATTERN.test(name))
    .map((name) => `screen name "${name}" must match ${SCREEN_NAME_PATTERN.source}`);
}

function validateEntries(
  screenNames: readonly string[],
  registry: DesktopStateRegistry,
): string[] {
  const screenSet = new Set(screenNames);
  const seen = new Set<string>();
  const errors: string[] = [];
  for (const entry of registry) {
    if (seen.has(entry.screen)) {
      errors.push(`screen "${entry.screen}" has more than one registry entry`);
    }
    seen.add(entry.screen);
    if (!screenSet.has(entry.screen)) {
      errors.push(`registry entry "${entry.screen}" is not in the screen registry`);
    }
    if (entry.maxState.kind === "none" && entry.maxState.noMaxStateReason.trim() === "") {
      errors.push(`screen "${entry.screen}" has maxState "none" but an empty noMaxStateReason`);
    }
  }
  return errors;
}

function validateCoverage(screenNames: readonly string[], registry: DesktopStateRegistry): string[] {
  const registrySet = new Set(registry.map((entry) => entry.screen));
  return screenNames
    .filter((name) => !registrySet.has(name))
    .map((name) => `screen "${name}" is missing a desktop state registry entry`);
}

export function validateDesktopStateRegistry(
  screenNames: readonly string[],
  registry: DesktopStateRegistry,
): RegistryValidationResult {
  const errors = [
    ...validateScreenNames(screenNames),
    ...validateEntries(screenNames, registry),
    ...validateCoverage(screenNames, registry),
  ];
  return { valid: errors.length === 0, errors };
}
