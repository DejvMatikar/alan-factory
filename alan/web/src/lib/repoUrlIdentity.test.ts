import { describe, expect, it } from "vitest";

import { parseRepoIdentity, repoIdentityErrorText, segmentCharsetErrorText } from "./repoUrlIdentity";

describe("parseRepoIdentity", () => {
  it("parses a bare owner/repo slug", () => {
    expect(parseRepoIdentity("acme/widget")).toEqual({ ok: true, owner: "acme", repo: "widget" });
  });

  it("parses a bare https URL", () => {
    expect(parseRepoIdentity("https://github.com/acme/widget")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("strips a .git suffix", () => {
    expect(parseRepoIdentity("https://github.com/acme/widget.git")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("strips a trailing slash", () => {
    expect(parseRepoIdentity("https://github.com/acme/widget/")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("accepts the www. subdomain", () => {
    expect(parseRepoIdentity("https://www.github.com/acme/widget")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("accepts a mixed-case host", () => {
    expect(parseRepoIdentity("https://GitHub.com/acme/widget")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("collapses a deep link to a file", () => {
    expect(parseRepoIdentity("https://github.com/acme/widget/blob/main/README.md")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("collapses a deep link to a pull request", () => {
    expect(parseRepoIdentity("https://github.com/acme/widget/pull/12")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("parses an scp-style SSH address", () => {
    expect(parseRepoIdentity("git@github.com:acme/widget.git")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("parses an ssh:// URL", () => {
    expect(parseRepoIdentity("ssh://git@github.com/acme/widget")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("resolves a scheme-less github.com paste instead of treating the host as the owner", () => {
    expect(parseRepoIdentity("github.com/acme/widget")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("resolves a scheme-less www.github.com paste", () => {
    expect(parseRepoIdentity("www.github.com/acme/widget")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("resolves a scheme-less github.com paste with a mixed-case host", () => {
    expect(parseRepoIdentity("GitHub.com/acme/widget")).toEqual({
      ok: true,
      owner: "acme",
      repo: "widget",
    });
  });

  it("treats a bare scheme-less github.com host with no path as unparsable", () => {
    expect(parseRepoIdentity("github.com")).toEqual({ ok: false, reason: "unparsable" });
  });

  it("refuses a non-GitHub host", () => {
    expect(parseRepoIdentity("https://gitlab.com/acme/widget")).toEqual({
      ok: false,
      reason: "not_github",
    });
  });

  it("treats an empty string as empty", () => {
    expect(parseRepoIdentity("")).toEqual({ ok: false, reason: "empty" });
  });

  it("treats a whitespace-only string as empty", () => {
    expect(parseRepoIdentity("   ")).toEqual({ ok: false, reason: "empty" });
  });

  it("treats plain text with no slash as unparsable", () => {
    expect(parseRepoIdentity("not a url at all")).toEqual({ ok: false, reason: "unparsable" });
  });

  it("treats a slug with an empty segment as unparsable", () => {
    expect(parseRepoIdentity("owner//repo")).toEqual({ ok: false, reason: "unparsable" });
  });

  it("preserves segment case verbatim, never folding it", () => {
    expect(parseRepoIdentity("Owner/Repo")).toEqual({ ok: true, owner: "Owner", repo: "Repo" });
  });
});

describe("repoIdentityErrorText", () => {
  it("names GitHub-only support for a non_github reason", () => {
    expect(repoIdentityErrorText("not_github")).toContain("github.com");
  });

  it("names the accepted input shapes for an unparsable reason", () => {
    expect(repoIdentityErrorText("unparsable")).toContain("owner/repo");
  });
});

describe("segmentCharsetErrorText", () => {
  it("returns null for an owner/repo within the ENGINE_SEGMENT_RE charset", () => {
    expect(segmentCharsetErrorText("acme", "widget")).toBeNull();
  });

  it("returns the segment help text when the owner falls outside the charset", () => {
    expect(segmentCharsetErrorText("acme!bad", "widget")).toContain(
      "only contain letters, numbers",
    );
  });

  it("returns the segment help text when the repo falls outside the charset", () => {
    expect(segmentCharsetErrorText("acme", "widget!bad")).toContain(
      "only contain letters, numbers",
    );
  });
});
