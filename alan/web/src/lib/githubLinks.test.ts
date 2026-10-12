import { describe, expect, it } from "vitest";

import { blobBaseUrl, blobUrl, commitUrl, pullUrl } from "./githubLinks";

describe("pullUrl", () => {
  it("builds a byte-exact pull URL for the default repo", () => {
    expect(pullUrl("DejvMatikar/alan", 42)).toBe("https://github.com/DejvMatikar/alan/pull/42");
  });

  it("builds a byte-exact pull URL for a non-default repo", () => {
    expect(pullUrl("acme-corp/sprocket", 7)).toBe("https://github.com/acme-corp/sprocket/pull/7");
  });
});

describe("commitUrl", () => {
  it("builds a byte-exact commit URL for the default repo", () => {
    expect(commitUrl("DejvMatikar/alan", "abc1234567abcdef1234567abcdef1234567abcd")).toBe(
      "https://github.com/DejvMatikar/alan/commit/abc1234567abcdef1234567abcdef1234567abcd",
    );
  });

  it("builds a byte-exact commit URL for a non-default repo", () => {
    expect(commitUrl("acme-corp/sprocket", "abc1234")).toBe(
      "https://github.com/acme-corp/sprocket/commit/abc1234",
    );
  });
});

describe("blobBaseUrl", () => {
  it("builds a byte-exact blob base URL for the default repo", () => {
    expect(blobBaseUrl("DejvMatikar/alan")).toBe("https://github.com/DejvMatikar/alan/blob/main/");
  });

  it("builds a byte-exact blob base URL for a non-default repo", () => {
    expect(blobBaseUrl("acme-corp/sprocket")).toBe("https://github.com/acme-corp/sprocket/blob/main/");
  });
});

describe("blobUrl", () => {
  it("builds a byte-exact blob URL for the default repo", () => {
    expect(blobUrl("DejvMatikar/alan", "docs/roadmap.md")).toBe(
      "https://github.com/DejvMatikar/alan/blob/main/docs/roadmap.md",
    );
  });

  it("builds a byte-exact blob URL for a non-default repo", () => {
    expect(blobUrl("acme-corp/sprocket", "config/engines.d/widget.yaml")).toBe(
      "https://github.com/acme-corp/sprocket/blob/main/config/engines.d/widget.yaml",
    );
  });
});
