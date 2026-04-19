"""
Шар роботи з базою даних SQLite.
"""
import json
import sqlite3
from typing import List, Optional
from datetime import datetime, timezone
from pathlib import Path

DB_FILE = Path("indexer.db")


def set_database(path: Path) -> None:
    """Встановлює шлях до файлу SQLite (викликати перед init_db та іншими операціями)."""
    global DB_FILE
    DB_FILE = path


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Створює таблиці якщо їх немає."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scans (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                folder       TEXT    NOT NULL,
                file         TEXT    NOT NULL,
                number       INTEGER,
                processed_at TEXT    NOT NULL,
                meta         TEXT,
                UNIQUE(folder, file)
            );

            CREATE TABLE IF NOT EXISTS persons (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id  INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                surname  TEXT,
                name     TEXT,
                meta     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_persons_scan ON persons(scan_id);
            CREATE INDEX IF NOT EXISTS idx_persons_surname ON persons(surname);
        """)
        _migrate_add_scans_meta_column(conn)
        _migrate_persons_name_surname_order(conn)
        _migrate_persons_legacy_columns_to_meta(conn)


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(r[1]) for r in sorted(rows, key=lambda r: r[0])]


def _migrate_add_scans_meta_column(conn: sqlite3.Connection) -> None:
    if "meta" not in _table_columns(conn, "scans"):
        conn.execute("ALTER TABLE scans ADD COLUMN meta TEXT")


def _migrate_persons_legacy_columns_to_meta(conn: sqlite3.Connection) -> None:
    """
    Старі БД: father, yob, location → JSON у persons.meta; колонки видаляються.
    Нові БД вже мають лише meta.
    """
    cols = _table_columns(conn, "persons")
    if "father" not in cols:
        if "meta" not in cols:
            conn.execute("ALTER TABLE persons ADD COLUMN meta TEXT")
        return

    if "meta" not in cols:
        conn.execute("ALTER TABLE persons ADD COLUMN meta TEXT")

    conn.execute(
        """
        UPDATE persons
        SET meta = json_object(
            'father', father,
            'yob', yob,
            'location', location
        )
        WHERE meta IS NULL
        """
    )

    _drop_person_legacy_columns(conn)


def _drop_person_legacy_columns(conn: sqlite3.Connection) -> None:
    """Видаляє father, yob, location; при відсутності DROP COLUMN — пересоздання таблиці."""
    try:
        conn.execute("ALTER TABLE persons DROP COLUMN father")
        conn.execute("ALTER TABLE persons DROP COLUMN yob")
        conn.execute("ALTER TABLE persons DROP COLUMN location")
    except sqlite3.OperationalError:
        conn.executescript("""
            BEGIN;
            CREATE TABLE persons__meta (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id  INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                surname  TEXT,
                name     TEXT,
                meta     TEXT
            );
            INSERT INTO persons__meta (id, scan_id, surname, name, meta)
            SELECT id, scan_id, surname, name, meta FROM persons;
            DROP TABLE persons;
            ALTER TABLE persons__meta RENAME TO persons;
            CREATE INDEX IF NOT EXISTS idx_persons_scan ON persons(scan_id);
            CREATE INDEX IF NOT EXISTS idx_persons_surname ON persons(surname);
            COMMIT;
        """)


def _migrate_persons_name_surname_order(conn: sqlite3.Connection) -> None:
    """Якщо таблиця persons ще у старому порядку (name, surname) — пересоздаємо з (surname, name)."""
    rows = conn.execute("PRAGMA table_info(persons)").fetchall()
    if not rows:
        return
    # cid -> name
    cols = [r[1] for r in sorted(rows, key=lambda r: r[0])]
    try:
        i_name = cols.index("name")
        i_surname = cols.index("surname")
    except ValueError:
        return
    if i_surname < i_name:
        return
    conn.executescript("""
        BEGIN;
        CREATE TABLE persons__reorder (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id  INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            surname  TEXT,
            name     TEXT,
            father   TEXT,
            yob      INTEGER,
            location TEXT
        );
        INSERT INTO persons__reorder (id, scan_id, surname, name, father, yob, location)
        SELECT id, scan_id, surname, name, father, yob, location FROM persons;
        DROP TABLE persons;
        ALTER TABLE persons__reorder RENAME TO persons;
        CREATE INDEX IF NOT EXISTS idx_persons_scan ON persons(scan_id);
        CREATE INDEX IF NOT EXISTS idx_persons_surname ON persons(surname);
        COMMIT;
    """)


def is_processed(folder: str, file: str) -> bool:
    """Перевіряє чи скан вже оброблявся."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM scans WHERE folder = ? AND file = ?", (folder, file)
        ).fetchone()
    return row is not None


def delete_scan(folder: str, file: str):
    """Видаляє скан і всіх пов'язаних людей (cascade)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM scans WHERE folder = ? AND file = ?", (folder, file)
        )


def _encode_person_meta(person: dict) -> Optional[str]:
    """Серіалізує persons.meta у JSON для SQLite TEXT."""
    m = person.get("meta")
    if m is None:
        return None
    if isinstance(m, str):
        s = m.strip()
        return s if s else None
    if isinstance(m, dict):
        return json.dumps(m, ensure_ascii=False)
    return None


def _encode_scan_meta(scan_meta: Optional[dict]) -> Optional[str]:
    if not scan_meta:
        return None
    return json.dumps(scan_meta, ensure_ascii=False)


def save_scan(
    folder: str,
    file: str,
    number: Optional[int],
    persons: List[dict],
    scan_meta: Optional[dict] = None,
):
    """Зберігає скан і список людей. scan_meta — JSON-поля для колонки scans.meta."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scans (folder, file, number, processed_at, meta) VALUES (?, ?, ?, ?, ?)",
            (folder, file, number, now, _encode_scan_meta(scan_meta)),
        )
        scan_id = cur.lastrowid
        if persons:
            conn.executemany(
                """INSERT INTO persons (scan_id, surname, name, meta)
                   VALUES (?, ?, ?, ?)""",
                [
                    (
                        scan_id,
                        p.get("surname"),
                        p.get("name"),
                        _encode_person_meta(p),
                    )
                    for p in persons
                ],
            )


def get_stats() -> dict:
    """Повертає загальну статистику."""
    with get_conn() as conn:
        scans = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        persons = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        last = conn.execute("SELECT MAX(processed_at) FROM scans").fetchone()[0]
        recent = conn.execute(
            """SELECT s.folder, s.file, s.processed_at, COUNT(p.id) as cnt
               FROM scans s LEFT JOIN persons p ON p.scan_id = s.id
               GROUP BY s.id ORDER BY s.processed_at DESC LIMIT 5"""
        ).fetchall()
    return {
        "scans": scans,
        "persons": persons,
        "last_processed": last,
        "recent": [dict(r) for r in recent],
    }
