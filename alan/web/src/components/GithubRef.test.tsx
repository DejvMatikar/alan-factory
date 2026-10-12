import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import GithubRef from "./GithubRef";

describe("GithubRef — resolved repo (link branch)", () => {
  it("renders a pull-request link with the exact href/text/target/rel/className passed in", () => {
    render(
      <GithubRef
        repo="acme-corp/sprocket"
        kind="pull"
        prNumber={42}
        className="text-btn-action underline"
        rel="noreferrer"
        testId="pr-link"
      >
        PR #42
      </GithubRef>,
    );
    const link = screen.getByTestId("pr-link");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "https://github.com/acme-corp/sprocket/pull/42");
    expect(link).toHaveTextContent("PR #42");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(link).toHaveClass("text-btn-action", "underline");
  });

  it("renders a commit link", () => {
    render(
      <GithubRef
        repo="DejvMatikar/alan"
        kind="commit"
        sha="abc1234567abcdef1234567abcdef1234567abcd"
        className="text-xs text-text-muted"
        rel="noopener noreferrer"
        testId="commit-link"
      >
        Commit artifact
      </GithubRef>,
    );
    const link = screen.getByTestId("commit-link");
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/DejvMatikar/alan/commit/abc1234567abcdef1234567abcdef1234567abcd",
    );
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("defaults target to _blank when omitted", () => {
    render(
      <GithubRef repo="acme/widget" kind="pull" prNumber={1} className="c" rel="noreferrer" testId="t">
        PR #1
      </GithubRef>,
    );
    expect(screen.getByTestId("t")).toHaveAttribute("target", "_blank");
  });

  it("is keyboard-reachable and shows a focus outline like any native link", async () => {
    const user = userEvent.setup();
    render(
      <GithubRef repo="acme/widget" kind="pull" prNumber={1} className="c" rel="noreferrer" testId="t">
        PR #1
      </GithubRef>,
    );
    await user.tab();
    expect(screen.getByTestId("t")).toHaveFocus();
  });
});

describe("GithubRef — unresolved repo (null branch)", () => {
  it("renders a non-clickable span carrying the neutral tokens and the unresolved wording, with the number visible", () => {
    render(
      <GithubRef repo={null} kind="pull" prNumber={42} className="text-btn-action underline" rel="noreferrer" testId="pr-link">
        PR #42
      </GithubRef>,
    );
    const el = screen.getByTestId("pr-link");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveTextContent("PR #42");
    expect(el).toHaveTextContent("repository unresolved");
    expect(el.className).toContain("bg-status-neutral-tint/15");
    expect(el.className).toContain("text-status-neutral-fg");
    expect(el.className).not.toContain("banner-error");
    expect(el.className).not.toContain("status-danger");
  });

  it("is not a link, carries no role=link, and is not in the tab order", async () => {
    const user = userEvent.setup();
    render(
      <GithubRef repo={null} kind="commit" sha="abc1234" className="c" rel="noreferrer" testId="t">
        Commit artifact
      </GithubRef>,
    );
    const el = screen.getByTestId("t");
    expect(el).not.toHaveAttribute("href");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    await user.tab();
    expect(el).not.toHaveFocus();
  });

  it("ignores the caller's resolved-state className", () => {
    render(
      <GithubRef repo={null} kind="pull" prNumber={1} className="text-blue-400 hover:text-blue-300" rel="noreferrer" testId="t">
        PR #1
      </GithubRef>,
    );
    expect(screen.getByTestId("t").className).not.toContain("text-blue-400");
  });
});
