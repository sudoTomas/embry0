/**
 * A repository's path in the API, and its sub-resources' (#257).
 *
 * A sub-resource follows a `/-/` separator, as GitLab's do:
 * `/repos/acme/products/qa-config/-/history` is the history of the
 * repository `acme/products/qa-config`. No segment of a repository is `-`, so
 * a repository named like a sub-resource is never read as one. The repository
 * itself is the separator with nothing after it, `/repos/<repo>/-/`: its path
 * without the separator is also the QA config of `acme/products` while the
 * server still answers the old paths, and is refused (409) when both are
 * registered. Every API module builds its `/repos/...` paths here.
 */
export function repoPath(fullRepo: string, subResource = ""): string {
  return `/repos/${fullRepo}/-/${subResource}`;
}
