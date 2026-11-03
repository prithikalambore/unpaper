"""Anonymous usage statistics and feedback, stored in a small SQLite file.

What is stored: daily counters (visitors, uploads, successful/failed conversions,
pages, OCR pages, modes, error codes, average duration) and feedback (yes/no plus
an optional comment the user chose to type). What is never stored: IP addresses,
file names, document content, cookies or any per-user identifier.

Unique visitors are counted with an in-memory set of HMAC'd IPs that is reset
every day and never written to disk (a restart can double-count that day).

View the numbers on the server with:
    docker compose -f docker-compose.prod.yml exec backend python -m app.stats
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sqlite3
import sys
import threading

log = logging.getLogger(__name__)

COUNTERS = (
    "visitors", "uploads", "converted", "failed", "pages", "ocr_pages",
    "mode_layout", "mode_clean", "duration_ms", "rate_limited", "feedback_yes", "feedback_no",
)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS daily (
    day TEXT PRIMARY KEY,
    {", ".join(f"{c} INTEGER NOT NULL DEFAULT 0" for c in COUNTERS)}
);
CREATE TABLE IF NOT EXISTS errors (
    day TEXT NOT NULL, code TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, code)
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    useful INTEGER NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 0
);
"""


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


class Stats:
    def __init__(self, path: str):
        self.path = path
        self.enabled = bool(path)
        self.lock = threading.Lock()
        self._visitor_day = ""
        self._visitors: set[str] = set()
        if self.enabled:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            db = self._connect()
            try:
                db.executescript(SCHEMA)
            finally:
                db.close()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _safe(self, fn, *args) -> None:
        """Statistics must never break a conversion."""
        if not self.enabled:
            return
        try:
            with self.lock:
                db = self._connect()
                try:
                    with db:  # commits, or rolls back on error
                        fn(db, *args)
                finally:
                    db.close()
        except Exception as exc:
            log.warning("stats write failed: %s", type(exc).__name__)

    @staticmethod
    def _bump(db: sqlite3.Connection, **inc: int) -> None:
        day = _today()
        db.execute("INSERT OR IGNORE INTO daily (day) VALUES (?)", (day,))
        sets = ", ".join(f"{k} = {k} + ?" for k in inc)  # keys come from COUNTERS only
        db.execute(f"UPDATE daily SET {sets} WHERE day = ?", (*inc.values(), day))

    # ------------------------------------------------------------------ recorders

    def visit(self, client_key: str) -> None:
        with self.lock:
            day = _today()
            if day != self._visitor_day:
                self._visitor_day, self._visitors = day, set()
            if client_key in self._visitors:
                return
            self._visitors.add(client_key)
        self._safe(lambda db: self._bump(db, visitors=1))

    def upload(self, mode: str) -> None:
        self._safe(lambda db: self._bump(db, uploads=1, **{f"mode_{mode}": 1}))

    def rate_limited(self) -> None:
        self._safe(lambda db: self._bump(db, rate_limited=1))

    def converted(self, pages: int, ocr_pages: int, duration_s: float) -> None:
        self._safe(lambda db: self._bump(db, converted=1, pages=pages, ocr_pages=ocr_pages,
                                         duration_ms=int(duration_s * 1000)))

    def failed(self, code: str) -> None:
        def write(db: sqlite3.Connection) -> None:
            self._bump(db, failed=1)
            db.execute("INSERT OR IGNORE INTO errors (day, code) VALUES (?, ?)", (_today(), code))
            db.execute("UPDATE errors SET count = count + 1 WHERE day = ? AND code = ?", (_today(), code))
        self._safe(write)

    def feedback(self, useful: bool, comment: str, mode: str, pages: int) -> None:
        def write(db: sqlite3.Connection) -> None:
            self._bump(db, **{"feedback_yes" if useful else "feedback_no": 1})
            db.execute(
                "INSERT INTO feedback (created, useful, comment, mode, pages) VALUES (?, ?, ?, ?, ?)",
                (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), int(useful), comment, mode, pages),
            )
        self._safe(write)

    # ------------------------------------------------------------------ reporting

    def report(self, days: int = 30) -> dict:
        since = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days - 1)).isoformat()
        db = self._connect()
        try:
            db.row_factory = sqlite3.Row
            daily = [dict(r) for r in db.execute("SELECT * FROM daily WHERE day >= ? ORDER BY day", (since,))]
            errors = [dict(r) for r in db.execute(
                "SELECT code, SUM(count) AS count FROM errors WHERE day >= ? GROUP BY code ORDER BY count DESC",
                (since,))]
            feedback = [dict(r) for r in db.execute(
                "SELECT created, useful, comment, mode, pages FROM feedback WHERE created >= ? ORDER BY id DESC",
                (since,))]
        finally:
            db.close()
        return {"daily": daily, "errors": errors, "feedback": feedback}


def _print_report(stats: Stats, days: int) -> None:
    data = stats.report(days)
    totals = {c: sum(d[c] for d in data["daily"]) for c in COUNTERS}
    print(f"Last {days} days (UTC)\n")
    cols = ("day", "visitors", "uploads", "converted", "failed", "rate_limited", "pages", "feedback_yes", "feedback_no")
    print("  ".join(f"{c:>12}" for c in cols))
    for d in data["daily"]:
        print("  ".join(f"{d[c]:>12}" for c in cols))
    print("  ".join(f"{(totals[c] if c != 'day' else 'total'):>12}" for c in cols))
    done = totals["converted"]
    print(f"\nSuccess rate: {done / max(1, done + totals['failed']):.0%}   "
          f"Avg time: {totals['duration_ms'] / max(1, done) / 1000:.1f}s   "
          f"Layout/clean: {totals['mode_layout']}/{totals['mode_clean']}   OCR pages: {totals['ocr_pages']}")
    votes = totals["feedback_yes"] + totals["feedback_no"]
    print(f"Found it useful: {totals['feedback_yes']} of {votes} answers")
    if data["errors"]:
        print("\nErrors:")
        for e in data["errors"]:
            print(f"  {e['count']:>6}  {e['code']}")
    comments = [f for f in data["feedback"] if f["comment"]]
    if comments:
        print("\nComments:")
        for f in comments:
            print(f"  [{f['created']}] {'useful' if f['useful'] else 'not useful'}: {f['comment']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show anonymous usage statistics and feedback.")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    path = os.environ.get("STATS_DB", "")
    if not path or not os.path.exists(path):
        print("No statistics yet (STATS_DB is not set or the file does not exist).")
        return 1
    _print_report(Stats(path), args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
