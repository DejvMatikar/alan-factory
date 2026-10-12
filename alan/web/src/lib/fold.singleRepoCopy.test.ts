import { describe, expect, it } from "vitest";

import {
  DEFAULT_KEY_STATES,
  LISTED_DEFAULT_KEY,
  makeConfiguredEngineSummaryFor,
  makeEngineSummaryFor,
  type DefaultKeyState,
} from "../test/engineFixtures";
import { switcherEntries } from "./fold";

const WIDGET_KEY = "acme/widget";

const DEFAULT_TITLE: Readonly<Record<DefaultKeyState, string>> = {
  equal: "Alan",
  other: "Listed Default",
};

describe("switcherEntries — single-repo copy", () => {
  it("lists a lone configured default as exactly one entry carrying its posture", () => {
    const entries = switcherEntries(
      [makeConfiguredEngineSummaryFor(LISTED_DEFAULT_KEY, { posture: "propose" })],
      LISTED_DEFAULT_KEY,
    );
    expect(entries).toEqual([{ key: LISTED_DEFAULT_KEY, title: "Listed Default", posture: "propose" }]);
  });

  it.each(DEFAULT_KEY_STATES)("lists a lone configured default under every default-key state (%s)", (state, listed) => {
    const defaultKey = listed;
    const entries = switcherEntries([makeConfiguredEngineSummaryFor(defaultKey)], defaultKey);
    expect(entries).toEqual([{ key: defaultKey, title: DEFAULT_TITLE[state], posture: "observe" }]);
  });

  it("lists nothing for a lone default whose forge target is null", () => {
    expect(
      switcherEntries([makeEngineSummaryFor(LISTED_DEFAULT_KEY, { forge: null })], LISTED_DEFAULT_KEY),
    ).toEqual([]);
  });

  it("lists nothing for a lone default whose forge target is absent", () => {
    expect(switcherEntries([makeEngineSummaryFor(LISTED_DEFAULT_KEY)], LISTED_DEFAULT_KEY)).toEqual([]);
  });

  it("gives a configured default beside a non-default the same entries as an unconfigured one", () => {
    const configured = switcherEntries(
      [makeConfiguredEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(WIDGET_KEY)],
      LISTED_DEFAULT_KEY,
    );
    const unconfigured = switcherEntries(
      [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(WIDGET_KEY)],
      LISTED_DEFAULT_KEY,
    );
    expect(configured).toEqual(unconfigured);
    expect(configured.map((entry) => entry.key)).toEqual([LISTED_DEFAULT_KEY, WIDGET_KEY]);
  });

  it.each(DEFAULT_KEY_STATES)("lists a lone non-default engine whatever the default key is (%s)", (_state, listed) => {
    const defaultKey = listed;
    expect(switcherEntries([makeEngineSummaryFor(WIDGET_KEY)], defaultKey)).toEqual([
      { key: WIDGET_KEY, title: "Widget", posture: "observe" },
    ]);
  });
});
