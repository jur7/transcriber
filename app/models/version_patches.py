import logging
from typing import List, Tuple, Callable, Dict

import sqlite3


# A single SQL patch step for a specific version
class PatchStep:
    def __init__(self, seq: int, sql: str, description: str = ""):
        self.seq = seq
        self.sql = sql
        self.description = description


# Registry mapping version string -> ordered list of PatchStep
# Define your DB schema/data migrations here. Keep versions in ascending order.
PATCHES: Dict[str, List[PatchStep]] = {
    # Example (uncomment and adjust when needed):
    # "0.1.1": [
    #     PatchStep(1, "ALTER TABLE transcriptions ADD COLUMN duration_seconds REAL", "Add duration column"),
    #     PatchStep(2, "CREATE INDEX IF NOT EXISTS idx_transcriptions_created_at ON transcriptions(created_at)", "Index on created_at"),
    # ],
}


def _ver_tuple(v: str) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split('.'))
    except Exception:
        return tuple()


def apply_patches_between(conn: sqlite3.Connection, from_version: str, to_version: str) -> None:
    """Apply all patches where version is > from_version and <= to_version.
    Patches within the same version are applied ordered by their seq.
    Executes within the provided sqlite3 connection.
    """
    if not from_version:
        start_tuple = tuple()
    else:
        start_tuple = _ver_tuple(from_version)
    end_tuple = _ver_tuple(to_version)

    # Collect versions within (from, to]
    candidates = [v for v in PATCHES.keys() if _ver_tuple(v) > start_tuple and _ver_tuple(v) <= end_tuple]
    candidates.sort(key=_ver_tuple)

    if not candidates:
        logging.info(f"[DB:MIGRATE] No patches to apply between {from_version or 'none'} -> {to_version}")
        return

    cur = conn.cursor()
    for ver in candidates:
        steps = sorted(PATCHES[ver], key=lambda s: s.seq)
        logging.info(f"[DB:MIGRATE] Applying {len(steps)} patch(es) for version {ver}")
        for step in steps:
            desc = f" (desc: {step.description})" if step.description else ""
            logging.info(f"[DB:MIGRATE] v{ver} step {step.seq}{desc}")
            cur.execute(step.sql)
    # Caller is responsible for commit/rollback

