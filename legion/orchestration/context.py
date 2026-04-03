"""Context injection — merges global, per-repo, and per-job context."""


def merge_context(
    global_context: str = "",
    repo_context: str = "",
    additional_context: str = "",
) -> str:
    parts = [p for p in [global_context, repo_context, additional_context] if p.strip()]
    return "\n\n".join(parts)
