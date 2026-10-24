"""Reset the demo database so every run starts fresh.

    python -m scripts.reset_demo              dry run: shows what would be deleted
    python -m scripts.reset_demo --yes        back up, then clear
    python -m scripts.reset_demo --yes --approvers    also clear granted approvers

The audit log is append-only inside the app (SQL triggers). This script is the one deliberate
exception: it is not importable from the bot, and it backs up the database first.
"""
import argparse
import sqlite3
import time
from pathlib import Path

from batchwatch import db

CLEARED = ("matches", "alert_rows", "processed_alerts", "audit_log")  # matches first: it points at alert_rows


def reset(path: Path, *, clear_approvers: bool = False, apply: bool = False,
          backup_dir: Path | None = None) -> dict:
    """Return {'counts': rows that were (or would be) deleted, 'backup': backup path or None}."""
    tables = CLEARED + (("approvers",) if clear_approvers else ())
    conn = sqlite3.connect(path, timeout=10)
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        if not apply:
            return {"counts": counts, "backup": None}

        backup_dir = backup_dir or db.DB_PATH.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"batchwatch-{time.strftime('%Y%m%d-%H%M%S')}.db"
        dest = sqlite3.connect(backup)
        conn.backup(dest)  # consistent copy even while the bot is running
        dest.close()

        sql = ["BEGIN IMMEDIATE;", "DROP TRIGGER IF EXISTS audit_no_delete;"]
        sql += [f"DELETE FROM {t};" for t in tables]
        if "AUTOINCREMENT" not in conn.execute("SELECT sql FROM sqlite_master WHERE name = 'matches'").fetchone()[0]:
            # older table: rebuild it so ids are never reused, keeping the id floor
            floor = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM matches").fetchone()[0])
            sql += ["DROP TABLE matches;", db.MATCHES_SQL]
            if floor:
                sql.append(f"INSERT INTO sqlite_sequence (name, seq) VALUES ('matches', {floor});")
        sql += [db.AUDIT_TRIGGERS, "COMMIT;"]
        try:
            conn.executescript("\n".join(sql))
        except Exception:
            conn.execute("ROLLBACK")
            raise
        db.log_event(conn, "reset_demo", "reset", f"cleared {counts}; backup {backup.name}")
        return {"counts": counts, "backup": backup}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually clear (otherwise dry run)")
    ap.add_argument("--approvers", action="store_true", help="also clear granted approvers")
    args = ap.parse_args(argv)
    if not db.DB_PATH.exists():
        raise SystemExit(f"No database at {db.DB_PATH}; nothing to reset.")
    result = reset(db.DB_PATH, clear_approvers=args.approvers, apply=args.yes)
    verb = "Deleted" if args.yes else "Would delete (dry run, add --yes)"
    print(f"{verb}: " + ", ".join(f"{t}={n}" for t, n in result["counts"].items()))
    if result["backup"]:
        print(f"Backup: {result['backup']}")


if __name__ == "__main__":
    main()
