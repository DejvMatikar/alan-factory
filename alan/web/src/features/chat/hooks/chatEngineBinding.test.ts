import { describe, expect, it } from "vitest";

import { DEFAULT_KEY_STATES, LISTED_DEFAULT_KEY, STUB_DEFAULT_ENGINE_KEY } from "../../../test/engineFixtures";
import { resolveBuildEngineKey, resolveExploreEngineKey } from "./chatEngineBinding";

describe("resolveBuildEngineKey", () => {
  it.each(DEFAULT_KEY_STATES)("uses the project's own engine key when a project context exists and it resolved (%s)", (_state, listed) => {
    expect(
      resolveBuildEngineKey({
        projectId: "proj-a",
        projectEngineKey: "acme-org/widgets",
        selectedEngineKey: undefined,
        onlyDefaultRepo: false,
        defaultKey: listed,
      }),
    ).toBe("acme-org/widgets");
  });

  it.each(DEFAULT_KEY_STATES)("returns null when a project context exists but its engine key is unresolved, never substituting the default (%s)", (_state, listed) => {
    expect(
      resolveBuildEngineKey({
        projectId: "proj-a",
        projectEngineKey: null,
        selectedEngineKey: undefined,
        onlyDefaultRepo: true,
        defaultKey: listed,
      }),
    ).toBeNull();
  });

  it.each(DEFAULT_KEY_STATES)("returns null for an unresolved project engine key given as undefined, never substituting the default (%s)", (_state, listed) => {
    expect(
      resolveBuildEngineKey({
        projectId: "proj-a",
        projectEngineKey: undefined,
        selectedEngineKey: undefined,
        onlyDefaultRepo: true,
        defaultKey: listed,
      }),
    ).toBeNull();
  });

  it.each(DEFAULT_KEY_STATES)("ignores selectedEngineKey and onlyDefaultRepo entirely once a project context exists (%s)", (_state, listed) => {
    expect(
      resolveBuildEngineKey({
        projectId: "proj-a",
        projectEngineKey: null,
        selectedEngineKey: "acme-org/widgets",
        onlyDefaultRepo: true,
        defaultKey: listed,
      }),
    ).toBeNull();
  });

  it.each(DEFAULT_KEY_STATES)("prefers the project's engine key over the selection and the default (%s)", (_state, listed) => {
    expect(
      resolveBuildEngineKey({
        projectId: "proj-a",
        projectEngineKey: "acme-org/widgets",
        selectedEngineKey: "acme-org/other",
        onlyDefaultRepo: true,
        defaultKey: listed,
      }),
    ).toBe("acme-org/widgets");
  });

  it.each(DEFAULT_KEY_STATES)("uses the explicitly selected engine key when there is no project context (%s)", (_state, listed) => {
    expect(
      resolveBuildEngineKey({
        projectId: undefined,
        projectEngineKey: undefined,
        selectedEngineKey: "acme-org/widgets",
        onlyDefaultRepo: false,
        defaultKey: listed,
      }),
    ).toBe("acme-org/widgets");
  });

  it.each(DEFAULT_KEY_STATES)("prefers the selection over the only-default fallback (%s)", (_state, listed) => {
    expect(
      resolveBuildEngineKey({
        projectId: undefined,
        projectEngineKey: undefined,
        selectedEngineKey: "acme-org/widgets",
        onlyDefaultRepo: true,
        defaultKey: listed,
      }),
    ).toBe("acme-org/widgets");
  });

  it.each(DEFAULT_KEY_STATES)("returns the passed default key only when no project context exists and only the default repo is registered (%s)", (_state, listed) => {
    const defaultKey = listed;
    expect(
      resolveBuildEngineKey({
        projectId: undefined,
        projectEngineKey: undefined,
        selectedEngineKey: undefined,
        onlyDefaultRepo: true,
        defaultKey,
      }),
    ).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("returns null when there is no project context, no selection, and more than one repo is registered (%s)", (_state, listed) => {
    expect(
      resolveBuildEngineKey({
        projectId: undefined,
        projectEngineKey: undefined,
        selectedEngineKey: undefined,
        onlyDefaultRepo: false,
        defaultKey: listed,
      }),
    ).toBeNull();
  });

  it("falls back to the listed default rather than the built-in key when they differ", () => {
    const fallback = resolveBuildEngineKey({
      projectId: undefined,
      projectEngineKey: undefined,
      selectedEngineKey: undefined,
      onlyDefaultRepo: true,
      defaultKey: LISTED_DEFAULT_KEY,
    });
    expect(fallback).toBe(LISTED_DEFAULT_KEY);
    expect(fallback).not.toBe(STUB_DEFAULT_ENGINE_KEY);
  });

  it("returns null rather than a repository when only the default repo is registered and no default is known", () => {
    expect(
      resolveBuildEngineKey({
        projectId: undefined,
        projectEngineKey: undefined,
        selectedEngineKey: undefined,
        onlyDefaultRepo: true,
        defaultKey: null,
      }),
    ).toBeNull();
  });

  it("treats the old built-in value as an ordinary selection under a different listed default", () => {
    expect(
      resolveBuildEngineKey({
        projectId: undefined,
        projectEngineKey: undefined,
        selectedEngineKey: STUB_DEFAULT_ENGINE_KEY,
        onlyDefaultRepo: true,
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    ).toBe(STUB_DEFAULT_ENGINE_KEY);
  });
});

describe("resolveExploreEngineKey", () => {
  it.each(DEFAULT_KEY_STATES)("uses the explicitly selected engine key when present (%s)", (_state, listed) => {
    expect(resolveExploreEngineKey("acme-org/widgets", false, listed)).toBe("acme-org/widgets");
  });

  it.each(DEFAULT_KEY_STATES)("prefers the selection over the only-default fallback (%s)", (_state, listed) => {
    expect(resolveExploreEngineKey("acme-org/widgets", true, listed)).toBe("acme-org/widgets");
  });

  it.each(DEFAULT_KEY_STATES)("returns the passed default key when nothing is selected and only the default repo is registered (%s)", (_state, listed) => {
    const defaultKey = listed;
    expect(resolveExploreEngineKey(undefined, true, defaultKey)).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("returns null when nothing is selected and more than one repo is registered (%s)", (_state, listed) => {
    expect(resolveExploreEngineKey(undefined, false, listed)).toBeNull();
  });

  it("falls back to the listed default rather than the built-in key when they differ", () => {
    const fallback = resolveExploreEngineKey(undefined, true, LISTED_DEFAULT_KEY);
    expect(fallback).toBe(LISTED_DEFAULT_KEY);
    expect(fallback).not.toBe(STUB_DEFAULT_ENGINE_KEY);
  });

  it("returns null rather than a repository when nothing is selected and no default is known", () => {
    expect(resolveExploreEngineKey(undefined, true, null)).toBeNull();
  });

  it("treats the old built-in value as an ordinary selection under a different listed default", () => {
    expect(resolveExploreEngineKey(STUB_DEFAULT_ENGINE_KEY, true, LISTED_DEFAULT_KEY)).toBe(
      STUB_DEFAULT_ENGINE_KEY,
    );
  });
});
