"""Guard against the migration graph forking.

Two PRs that each add a migration off the same parent merge cleanly in git but leave Alembic
with two heads, and `alembic upgrade head` then refuses to run at all — which is how 0.16.0
shipped with a backend that never became ready. Whoever merges second has to add a merge
revision (`alembic merge heads`); this test is what tells them.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_graph_has_a_single_head() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, (
        f"Alembic has {len(heads)} heads ({', '.join(sorted(heads))}); "
        "run `alembic merge heads` in backend/ to join them"
    )
