"""Which database this process is running against — the real one, or the sample workspace.

Decided at start-up by app/bootstrap.py, which records it in the STOCKSMITH_WORKSPACE environment
variable. Switching only writes a request for the next start; the frontend then restarts the app.
See bootstrap.py's "Sample workspace" section for the mechanics.
"""

import os

from app import bootstrap


def current() -> str:
    """"real" or "sample". "real" when bootstrap didn't run (the `uvicorn` dev loop)."""
    return os.environ.get(bootstrap.WORKSPACE_ENV, "real")


def switching_available() -> bool:
    """Only the packaged app starts through bootstrap, which is what acts on a request."""
    return bootstrap.WORKSPACE_ENV in os.environ


def request(mode: str) -> None:
    bootstrap.request_workspace(mode)
