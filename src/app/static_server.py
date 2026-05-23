"""Static file server for layer artifacts.

The Streamlit app talks to this server (default port 8502) to fetch
PNGs and GeoParquet files instead of embedding them as base64 inside
the Folium HTML. Without this, ten 30-MB Master Plan PNGs would blow
through Streamlit's 200 MB websocket payload limit.

The server is intentionally minimal: one route that serves any file
under the configured root, with a CORS allow-all policy because the
Streamlit page (localhost:8501) needs to fetch from localhost:8502.

It is launched as a background process by ``streamlit_app.py``; users
do not start it directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn

logger = logging.getLogger(__name__)

# Default port. Streamlit uses 8501; we sit one above.
DEFAULT_PORT = 8502


def create_app(serve_root: Path) -> FastAPI:
    """Build a FastAPI app that serves files under ``serve_root``.

    The endpoint accepts any path under serve_root and returns the file.
    Requests trying to escape the root (../) are rejected.

    Args:
        serve_root: Absolute path to the directory whose contents may be
            served. Anything outside this directory is forbidden.

    Returns:
        A configured FastAPI instance ready for uvicorn.
    """
    serve_root = serve_root.resolve()
    app = FastAPI(title="Construction Viability Map — static files")

    # Streamlit page (localhost:8501) calls us from a different origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/files/{file_path:path}")
    def get_file(file_path: str):
        """Return the file at ``serve_root / file_path``.

        Rejects path traversal attempts and missing files.
        """
        candidate = (serve_root / file_path).resolve()
        try:
            candidate.relative_to(serve_root)
        except ValueError:
            raise HTTPException(status_code=403, detail="Path outside server root")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(candidate)

    @app.get("/health")
    def health():
        """Liveness probe used by the Streamlit launcher to wait for startup."""
        return {"status": "ok", "serve_root": str(serve_root)}

    return app


def run(serve_root: Path, port: int = DEFAULT_PORT) -> None:
    """Run the static server (blocking). Used by the launcher subprocess."""
    app = create_app(serve_root)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    # Allow `python -m src.app.static_server <serve_root>` for manual debugging.
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.app.static_server <serve_root> [port]")
        sys.exit(1)
    root = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    run(root, port)