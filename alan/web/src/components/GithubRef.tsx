import type { ReactNode } from "react";

import { commitUrl, pullUrl } from "../lib/githubLinks";

interface GithubRefCommonProps {
  repo: string | null;
  className: string;
  rel: string;
  target?: string;
  testId?: string;
  children: ReactNode;
}

export type GithubRefProps =
  | (GithubRefCommonProps & { kind: "pull"; prNumber: number })
  | (GithubRefCommonProps & { kind: "commit"; sha: string });

const UNRESOLVED_CLASS =
  "inline-block rounded px-2 py-0.5 text-xs bg-status-neutral-tint/15 text-status-neutral-fg";

function hrefFor(props: Readonly<GithubRefProps>, repo: string): string {
  return props.kind === "pull" ? pullUrl(repo, props.prNumber) : commitUrl(repo, props.sha);
}

export default function GithubRef(props: Readonly<GithubRefProps>) {
  const { repo, className, rel, target = "_blank", testId, children } = props;
  if (repo === null) {
    return (
      <span data-testid={testId} className={UNRESOLVED_CLASS}>
        {children} · repository unresolved
      </span>
    );
  }
  return (
    <a href={hrefFor(props, repo)} className={className} target={target} rel={rel} data-testid={testId}>
      {children}
    </a>
  );
}
