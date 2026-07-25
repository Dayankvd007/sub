"""Entry point for the local API service (`subtitle-api`).

Binds to 127.0.0.1 by default and never to 0.0.0.0: this is a single-user local
tool, and the backend holds provider credentials.
"""

from __future__ import annotations

import sys

from .app import create_app
from .deps import get_settings


def main(argv: list[str] | None = None) -> int:
    try:
        import uvicorn
    except ImportError:  # pragma: no cover - depends on install extras
        print(
            "error: the API extra is not installed. Run: pip install -e \".[api,dev]\"",
            file=sys.stderr,
        )
        return 2

    settings = get_settings()

    if not settings.allowed_origins:
        print(
            "warning: ALLOWED_ORIGINS is empty — browser requests from the "
            "extension will be blocked by CORS. Set it to your "
            "chrome-extension://<id> origin.",
            file=sys.stderr,
        )
    if not settings.api_key_present:
        print(
            f"warning: no API key found for provider {settings.provider!r}; "
            "/translate will fail until it is set in the environment.",
            file=sys.stderr,
        )

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
