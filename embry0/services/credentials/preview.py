"""What an operator sees of a secret: never the value."""

_GLYPHS = "••••••••"


def masked_preview(secret: str) -> str:
    """First 4 + last 4 characters when the secret is 12+ long, else all glyphs.

    Not the env-var rule (api.v1.environment.partial_mask shows at most the
    last 2 characters): this preview is stored with the credential when it is
    written, so changing it means rewriting the stored rows.
    """
    if len(secret) < 12:
        return _GLYPHS
    return f"{secret[:4]}{_GLYPHS}{secret[-4:]}"
