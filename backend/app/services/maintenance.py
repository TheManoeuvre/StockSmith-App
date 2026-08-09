"""Whether the backend is currently refusing normal work, and why.

Plain module-level state, deliberately — the same justification `sync_scheduler` documents for
its `_tasks` dict. A single uvicorn process serves the whole desktop app (one host machine runs
the backend; other devices are thin clients pointing at it), so there is exactly one place this
flag could live and no coordination problem to solve.

Not persisted. A restore's intent survives a restart in `restore/RESTORE_PENDING.json`, which is
the durable record; this flag only describes the *running process*, and a process that has just
started is by definition not mid-restore. Recovering the flag on boot would in fact be wrong —
bootstrap applies any staged restore before the app serves its first request, so by the time
this module is importable the work is already done.

Phase 4 (restore) is what calls `enter()`/`exit()`. Until then the backend is always available
and this reports so, which keeps /system/status honest rather than hard-coding "ok".
"""

from typing import Literal

Phase = Literal["restore_staged", "restoring"]

_phase: Phase | None = None


def enter(phase: Phase) -> None:
    global _phase
    _phase = phase


def exit() -> None:  # noqa: A001 — reads as maintenance.exit() at the call site
    global _phase
    _phase = None


def current_phase() -> Phase | None:
    return _phase


def is_active() -> bool:
    return _phase is not None
