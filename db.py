"""
SQLite persistence layer for Spider Tutor.

Tables
------
subjects          -- per-user named subjects (courses)
notes_files       -- generated notes PDFs saved to storage/notes/ with UUID filenames
subject_progress  -- per-user-per-subject streak + achievement flags
accuracy_history  -- append-only quiz scores, one row per session
"""

import os
import sqlite3
from datetime import date as _date, datetime, timezone

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(BASE_DIR, "app.db")
NOTES_DIR = os.path.join(BASE_DIR, "storage", "notes")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    os.makedirs(NOTES_DIR, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS subjects (
              id          TEXT PRIMARY KEY,
              username    TEXT NOT NULL,
              name        TEXT NOT NULL,
              created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notes_files (
              id                TEXT PRIMARY KEY,
              username          TEXT NOT NULL,
              subject_id        TEXT,
              original_filename TEXT,
              file_path         TEXT NOT NULL,
              created_at        TEXT NOT NULL,
              FOREIGN KEY (subject_id) REFERENCES subjects(id)
            );

            CREATE TABLE IF NOT EXISTS subject_progress (
              username         TEXT NOT NULL,
              subject_id       TEXT NOT NULL,
              streak_current   INTEGER DEFAULT 0,
              streak_best      INTEGER DEFAULT 0,
              last_active_date TEXT,
              first_quiz       INTEGER DEFAULT 0,
              three_day_streak INTEGER DEFAULT 0,
              seven_day_streak INTEGER DEFAULT 0,
              comeback         INTEGER DEFAULT 0,
              perfectionist    INTEGER DEFAULT 0,
              high_achiever    INTEGER DEFAULT 0,
              PRIMARY KEY (username, subject_id),
              FOREIGN KEY (subject_id) REFERENCES subjects(id)
            );

            CREATE TABLE IF NOT EXISTS accuracy_history (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              username   TEXT NOT NULL,
              subject_id TEXT NOT NULL,
              date       TEXT NOT NULL,
              accuracy   REAL NOT NULL
            );
        """)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── subjects helpers ──────────────────────────────────────────────────────────

def insert_subject(id: str, username: str, name: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subjects (id, username, name, created_at) VALUES (?, ?, ?, ?)",
            (id, username, name, now_iso()),
        )


def get_subjects_for_user(username: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM subjects WHERE username = ? ORDER BY created_at",
            (username,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── notes_files helpers ───────────────────────────────────────────────────────

def insert_notes_file(
    id: str,
    username: str,
    file_path: str,
    original_filename: str,
    subject_id: str | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO notes_files
               (id, username, subject_id, original_filename, file_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (id, username, subject_id, original_filename, file_path, now_iso()),
        )


def get_notes_file(file_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM notes_files WHERE id = ?", (file_id,)
        ).fetchone()
    return dict(row) if row else None


# ── progress helpers ──────────────────────────────────────────────────────────

def _days_between(a: str, b: str) -> int:
    """Signed whole-day difference (b − a) for 'YYYY-MM-DD' strings."""
    ay, am, ad = map(int, a.split("-"))
    by, bm, bd = map(int, b.split("-"))
    return (_date(by, bm, bd) - _date(ay, am, ad)).days


def get_subject_progress(username: str, subject_id: str) -> dict:
    """Return the canonical progress shape for one user+subject."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM subject_progress WHERE username = ? AND subject_id = ?",
            (username, subject_id),
        ).fetchone()

        history = conn.execute(
            """SELECT date, accuracy FROM accuracy_history
               WHERE username = ? AND subject_id = ?
               ORDER BY id ASC""",
            (username, subject_id),
        ).fetchall()

    if row is None:
        return {
            "streak": {"current": 0, "best": 0},
            "accuracyHistory": [],
            "achievements": {
                "firstQuiz": False, "threeDayStreak": False,
                "sevenDayStreak": False, "comeback": False,
                "perfectionist": False, "highAchiever": False,
            },
        }

    row = dict(row)
    return {
        "streak": {"current": row["streak_current"], "best": row["streak_best"]},
        "accuracyHistory": [{"date": h["date"], "accuracy": h["accuracy"]} for h in history],
        "achievements": {
            "firstQuiz":      bool(row["first_quiz"]),
            "threeDayStreak": bool(row["three_day_streak"]),
            "sevenDayStreak": bool(row["seven_day_streak"]),
            "comeback":       bool(row["comeback"]),
            "perfectionist":  bool(row["perfectionist"]),
            "highAchiever":   bool(row["high_achiever"]),
        },
    }


def record_quiz_result(
    username: str, subject_id: str, correct: int, total: int, date_str: str
) -> dict:
    """
    Atomically insert an accuracy_history row, update the subject_progress
    streak and achievement flags, and return the updated progress shape.
    All business logic lives here; the route just validates input.
    """
    accuracy = round((correct / total * 100) if total > 0 else 0.0, 1)

    with get_db() as conn:
        # 1. Append to history first so the high_achiever check includes this session.
        conn.execute(
            "INSERT INTO accuracy_history (username, subject_id, date, accuracy) VALUES (?, ?, ?, ?)",
            (username, subject_id, date_str, accuracy),
        )

        # 2. Get or create the progress row.
        row = conn.execute(
            "SELECT * FROM subject_progress WHERE username = ? AND subject_id = ?",
            (username, subject_id),
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO subject_progress (username, subject_id) VALUES (?, ?)",
                (username, subject_id),
            )
            row = conn.execute(
                "SELECT * FROM subject_progress WHERE username = ? AND subject_id = ?",
                (username, subject_id),
            ).fetchone()

        row = dict(row)

        # 3. Streak logic.
        streak_current = row["streak_current"]
        streak_best    = row["streak_best"]
        last_active    = row["last_active_date"]
        comeback       = row["comeback"]

        if last_active == date_str:
            pass  # multiple quizzes on same day: only count once
        elif last_active and _days_between(last_active, date_str) == 1:
            streak_current += 1
        else:
            # Gap of ≥2 days, or very first quiz.
            if last_active and streak_current > 0:
                comeback = 1  # had a streak before this gap
            streak_current = 1

        streak_best = max(streak_best, streak_current)

        # 4. Achievement flags (monotonically increasing — never unset).
        total_sessions = conn.execute(
            "SELECT COUNT(*) FROM accuracy_history WHERE username = ? AND subject_id = ?",
            (username, subject_id),
        ).fetchone()[0]

        recent_rows = conn.execute(
            """SELECT accuracy FROM accuracy_history
               WHERE username = ? AND subject_id = ?
               ORDER BY id DESC LIMIT 5""",
            (username, subject_id),
        ).fetchall()
        recent_accs = [r[0] for r in recent_rows]
        avg_recent  = sum(recent_accs) / len(recent_accs) if recent_accs else 0.0

        first_quiz       = 1 if total_sessions >= 1      else row["first_quiz"]
        three_day_streak = 1 if streak_current >= 3      else row["three_day_streak"]
        seven_day_streak = 1 if streak_current >= 7      else row["seven_day_streak"]
        perfectionist    = 1 if accuracy == 100.0        else row["perfectionist"]
        high_achiever    = 1 if avg_recent >= 90.0       else row["high_achiever"]

        # 5. Persist.
        conn.execute(
            """UPDATE subject_progress SET
                 streak_current   = ?,
                 streak_best      = ?,
                 last_active_date = ?,
                 first_quiz       = ?,
                 three_day_streak = ?,
                 seven_day_streak = ?,
                 comeback         = ?,
                 perfectionist    = ?,
                 high_achiever    = ?
               WHERE username = ? AND subject_id = ?""",
            (
                streak_current, streak_best, date_str,
                first_quiz, three_day_streak, seven_day_streak,
                comeback, perfectionist, high_achiever,
                username, subject_id,
            ),
        )

    return get_subject_progress(username, subject_id)
