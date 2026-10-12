import { describe, expect, it } from "vitest";

import { emptyMetric, healthWindow, metricWindows } from "../test/healthWindows";
import {
  compactCell,
  comparisonSentence,
  coverageNote,
  formatPercent,
  oldestTimestamp,
  readFunnel,
  readWindow,
  verdictDisplay,
} from "./metricWindow";

describe("formatPercent", () => {
  it("rounds a ratio to a whole percent", () => {
    expect(formatPercent(0.846)).toBe("85%");
    expect(formatPercent(0)).toBe("0%");
  });
});

describe("readWindow", () => {
  it("reads a window with no value as unmeasured", () => {
    expect(readWindow(healthWindow(null, 0))).toEqual({ measured: false });
  });

  it("reads a window with a value but no samples as unmeasured", () => {
    expect(readWindow(healthWindow(0.5, 0))).toEqual({ measured: false });
  });

  it("reads a window with samples but no value as unmeasured", () => {
    expect(readWindow(healthWindow(null, 4))).toEqual({ measured: false });
  });

  it("marks a window under ten samples as low data while keeping its value", () => {
    expect(readWindow(healthWindow(0.5, 9))).toEqual({
      measured: true,
      value: 0.5,
      percent: "50%",
      sample: 9,
      lowData: true,
    });
  });

  it("treats exactly ten samples as enough data", () => {
    expect(readWindow(healthWindow(0.8, 10))).toMatchObject({ lowData: false, percent: "80%" });
  });
});

describe("readFunnel", () => {
  it("reads an empty funnel as not measured yet", () => {
    expect(readFunnel(0, 0)).toEqual({ measured: false, text: "not measured yet", lowData: false });
  });

  it("reads a stage as x of N and flags a small denominator as low data", () => {
    expect(readFunnel(3, 8)).toEqual({ measured: true, text: "3 of 8", lowData: true });
  });

  it("does not flag a denominator of ten or more", () => {
    expect(readFunnel(12, 20)).toEqual({ measured: true, text: "12 of 20", lowData: false });
  });
});

describe("coverageNote", () => {
  it("is silent when the window covers every day it claims", () => {
    expect(coverageNote(healthWindow(0.5, 20, 30), 30)).toBeNull();
  });

  it("is silent when there is nothing measured to qualify", () => {
    expect(coverageNote(healthWindow(null, 0, 3), 30)).toBeNull();
  });

  it("says the window covers less than a day", () => {
    expect(coverageNote(healthWindow(0.5, 20, 0), 30)).toBe("covers under 1 day");
  });

  it("says the window covers one day in the singular", () => {
    expect(coverageNote(healthWindow(0.5, 20, 1), 30)).toBe("covers only 1 day");
  });

  it("says how many days a shortened window covers", () => {
    expect(coverageNote(healthWindow(0.5, 20, 12), 30)).toBe("covers only 12 days");
  });
});

describe("verdictDisplay", () => {
  it.each([
    ["better", "▲", "Better"],
    ["worse", "▼", "Worse"],
    ["same", "≈", "About the same"],
    ["insufficient", null, "Not enough data to compare"],
  ] as const)("shows %s as a symbol and a word", (verdict, symbol, word) => {
    expect(verdictDisplay(verdict)).toEqual({ symbol, word });
  });
});

describe("comparisonSentence", () => {
  const strong = healthWindow(0.9, 40);
  const weak = healthWindow(0.7, 120);

  it("states both windows and a better verdict in full", () => {
    const metric = metricWindows(strong, weak, "better");
    expect(comparisonSentence("Outcome rate", metric)).toBe(
      "Outcome rate: last 7 days 90%, n=40; last 30 days 70%, n=120; the last 7 days are better than the last 30 days.",
    );
  });

  it("states a worse verdict", () => {
    expect(comparisonSentence("Outcome rate", metricWindows(weak, strong, "worse"))).toContain(
      "the last 7 days are worse than the last 30 days.",
    );
  });

  it("names the five point band for an about-the-same verdict", () => {
    expect(comparisonSentence("Outcome rate", metricWindows(strong, strong, "same"))).toContain(
      "about the same as the last 30 days, within 5 percentage points.",
    );
  });

  it("says lower is better when a decided verdict belongs to a lower-is-better metric", () => {
    const metric = metricWindows(healthWindow(0.05, 40), healthWindow(0.2, 90), "better", false);
    expect(comparisonSentence("Format error rate", metric)).toContain(
      "better than the last 30 days (lower is better for this metric).",
    );
  });

  it("does not mention direction for an undecided verdict on a lower-is-better metric", () => {
    const metric = metricWindows(healthWindow(0.05, 4), healthWindow(0.2, 90), "insufficient", false);
    expect(comparisonSentence("Format error rate", metric)).not.toContain("lower is better");
  });

  it("marks low data and a shortened window inside the sentence", () => {
    const metric = metricWindows(healthWindow(0.5, 4), healthWindow(0.6, 30, 12), "insufficient");
    const sentence = comparisonSentence("Build success rate", metric);
    expect(sentence).toContain("last 7 days 50%, n=4, low data;");
    expect(sentence).toContain("last 30 days 60%, n=30, covers only 12 days;");
    expect(sentence).toContain("not enough data to compare");
  });

  it("reads empty windows as not measured yet", () => {
    expect(comparisonSentence("Rescue load rate", emptyMetric())).toBe(
      "Rescue load rate: last 7 days not measured yet; last 30 days not measured yet; there is not enough data to compare the last 7 days with the last 30 days.",
    );
  });
});

describe("compactCell", () => {
  it("shows the 30-day value with the verdict symbol and no sample size when data is enough", () => {
    const cell = compactCell("Outcome rate", metricWindows(healthWindow(0.9, 40), healthWindow(0.7, 120), "better"));
    expect(cell).toMatchObject({ text: "70%", sampleText: null, lowData: false, symbol: "▲" });
    expect(cell.sentence).toContain("Outcome rate: last 7 days 90%, n=40");
  });

  it("dims a low-data 30-day value and shows its sample size", () => {
    const cell = compactCell("Outcome rate", metricWindows(healthWindow(0.9, 4), healthWindow(0.7, 6)));
    expect(cell).toMatchObject({ text: "70%", sampleText: "n=6", lowData: true, symbol: null });
  });

  it("shows an em dash when the 30-day window has no data", () => {
    expect(compactCell("Outcome rate", emptyMetric())).toMatchObject({
      text: "—",
      sampleText: null,
      lowData: false,
      symbol: null,
    });
  });
});

describe("oldestTimestamp", () => {
  it("is null when no source has a timestamp", () => {
    expect(oldestTimestamp([])).toBeNull();
    expect(oldestTimestamp([null, null])).toBeNull();
  });

  it("returns the only timestamp present", () => {
    expect(oldestTimestamp([null, "2026-09-24T10:00:00Z"])).toBe("2026-09-24T10:00:00Z");
  });

  it("returns the earliest of several timestamps", () => {
    expect(
      oldestTimestamp(["2026-09-24T10:00:00Z", "2026-09-24T08:00:00Z", "2026-09-24T09:00:00Z"]),
    ).toBe("2026-09-24T08:00:00Z");
  });
});
