function repoBase(repo: string): string {
  return `https://github.com/${repo}`;
}

export function pullUrl(repo: string, prNumber: number): string {
  return `${repoBase(repo)}/pull/${prNumber}`;
}

export function commitUrl(repo: string, sha: string): string {
  return `${repoBase(repo)}/commit/${sha}`;
}

export function blobBaseUrl(repo: string): string {
  return `${repoBase(repo)}/blob/main/`;
}

export function blobUrl(repo: string, path: string): string {
  return `${blobBaseUrl(repo)}${path}`;
}
