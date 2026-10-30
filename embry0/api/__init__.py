"""API layer — FastAPI application and endpoints."""

# Registers the `{full_repo:repo}` path convertor before any router module declares a /repos route (#243).
import embry0.api.repo_route  # noqa: F401
