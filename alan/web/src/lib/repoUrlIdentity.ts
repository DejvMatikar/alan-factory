import { ENGINE_SEGMENT_RE } from "../api";

export type RepoIdentityParse =
  | { ok: true; owner: string; repo: string }
  | { ok: false; reason: "empty" | "not_github" | "unparsable" };

const GITHUB_HOSTS = new Set(["github.com", "www.github.com"]);

function stripGitSuffix(segment: string): string {
  return segment.endsWith(".git") ? segment.slice(0, -4) : segment;
}

interface HostAndPath {
  host: string;
  path: string;
}

// A scheme-less "host/path" paste (github.com/owner/repo, or the
// www.github.com equivalent) has no "://" for the URL constructor to key
// off, so it fails to parse as a URL — without this check it would fall
// through to the plain-slug interpretation below and mis-resolve to
// owner="github.com". Recognized by plain string ops (not a regex) against
// the same GITHUB_HOSTS set the parsed-URL path checks, so the two paths
// can never drift on which hosts count.
function schemelessGithubHostAndPath(input: string): HostAndPath | null {
  const slashIndex = input.indexOf("/");
  const firstSegment = slashIndex === -1 ? input : input.slice(0, slashIndex);
  if (!GITHUB_HOSTS.has(firstSegment.toLowerCase())) return null;
  return { host: firstSegment, path: slashIndex === -1 ? "" : input.slice(slashIndex) };
}

// Handles https://, ssh://, and scp-style git@github.com:owner/repo — the
// scp form has no "://" so the URL constructor can't parse it; everything
// else (including a bare owner/repo slug, which has no scheme at all and
// makes `new URL` throw) falls through to an empty host, read by the caller
// as "not a URL, try it as a slug directly" — unless it's a scheme-less
// GitHub host paste, which is recognized explicitly first.
function hostAndPathOf(input: string): HostAndPath {
  const scpMatch = /^git@([^:]+):(.+)$/.exec(input);
  if (scpMatch !== null) {
    // Both groups are non-optional in the pattern above, so a successful
    // match always captures them — noUncheckedIndexedAccess still types the
    // access as possibly undefined, hence the assertion rather than an
    // unreachable (and uncoverable) `?? ""` fallback.
    return { host: scpMatch[1]!, path: scpMatch[2]! };
  }
  try {
    const url = new URL(input);
    return { host: url.hostname, path: url.pathname };
  } catch {
    return schemelessGithubHostAndPath(input) ?? { host: "", path: input };
  }
}

// Plain index scanning instead of a regex — no quantifier left for a linter
// to flag as super-linear, and slashes are the only character being counted.
function trimSlashes(path: string): string {
  let start = 0;
  while (start < path.length && path[start] === "/") start++;
  let end = path.length;
  while (end > start && path[end - 1] === "/") end--;
  return path.slice(start, end);
}

// Trailing-slash strip, then the first two path segments only — a deep link
// like /blob/main/x or /pull/12 collapses to the owner/repo it's nested
// under, and a .git suffix on the repo segment is stripped.
function slugSegmentsOf(path: string): [owner: string, repo: string] | null {
  const trimmed = trimSlashes(path);
  if (trimmed === "") return null;
  const parts = trimmed.split("/");
  const owner = parts[0];
  const repoRaw = parts[1];
  if (owner === undefined || repoRaw === undefined || owner === "" || repoRaw === "") {
    return null;
  }
  return [owner, stripGitSuffix(repoRaw)];
}

// Owner/repo are returned verbatim, never lower-cased — the slug is a path
// component and ENGINE_SEGMENT_RE is case-permissive, so folding case would
// change identity. Only the host is compared case-insensitively.
export function parseRepoIdentity(input: string): RepoIdentityParse {
  const trimmed = input.trim();
  if (trimmed === "") return { ok: false, reason: "empty" };
  const { host, path } = hostAndPathOf(trimmed);
  if (host !== "" && !GITHUB_HOSTS.has(host.toLowerCase())) {
    return { ok: false, reason: "not_github" };
  }
  const segments = slugSegmentsOf(path);
  if (segments === null) return { ok: false, reason: "unparsable" };
  const [owner, repo] = segments;
  return { ok: true, owner, repo };
}

// Shared inline-validation copy for both identity surfaces (the connect
// wizard's step 1 and the Settings credentials form) — centralized so the
// two never drift and never duplicate the same literal strings.
export function repoIdentityErrorText(reason: "not_github" | "unparsable"): string {
  return reason === "not_github"
    ? "Only github.com repositories are supported."
    : "Enter a GitHub repository URL or an owner/repo slug.";
}

export const SEGMENT_HELP_TEXT =
  "Owner and repository may only contain letters, numbers, '.', '_' and '-'.";

// Shared by both identity surfaces: a URL/slug can parse cleanly and still
// carry an owner or repo outside the charset api.ts's assertSafeSegment
// enforces server-side (ENGINE_SEGMENT_RE) — centralized so neither surface
// can drift from that gate or from each other's copy.
export function segmentCharsetErrorText(owner: string, repo: string): string | null {
  return ENGINE_SEGMENT_RE.test(owner) && ENGINE_SEGMENT_RE.test(repo) ? null : SEGMENT_HELP_TEXT;
}
