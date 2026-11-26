"""SQLite database and auditable updates for Cinema Monitor."""
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "cinema.sqlite"


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("""CREATE TABLE IF NOT EXISTS movies
        (id TEXT PRIMARY KEY, title TEXT, premiere TEXT, source TEXT, payload TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS changes
        (id INTEGER PRIMARY KEY AUTOINCREMENT, movie_id TEXT, detected_at TEXT,
         field TEXT, old_value TEXT, new_value TEXT, source TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS runs
        (id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT, source TEXT,
         status TEXT, details TEXT, found INTEGER DEFAULT 0)""")
    conn.commit()
    return conn


def now_utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def valid_date(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        date = dt.date.fromisoformat(value)
    except ValueError:
        return None
    return value if 2000 <= date.year <= 2100 else None


def source_rank(source):
    value = str(source).lower()
    # Premiere calendar entries are the authoritative published schedule.
    # Specific dated calendar pages take priority over month listings.
    if "календарь кинопоиска" in value and "ежедневная проверка" in value:
        return 4
    if "календарь кинопоиска" in value:
        return 3
    if "карточка кинопоиска" in value:
        return 2
    return 0


def upsert(conn, film, source):
    """Merge only populated verified fields without dropping existing details."""
    fid = str(film.get("id") or film.get("ID фильма") or "").strip()
    if not fid.isdigit():
        raise ValueError("Only Kinopoisk movie IDs are allowed")
    fields = {key: value for key, value in film.items()
              if key != "id" and value is not None and value != "" and value != []}
    fields["ID фильма"] = fid
    if "Дата премьеры" in fields:
        date = valid_date(fields["Дата премьеры"])
        if date:
            fields["Дата премьеры"] = date
        else:
            fields.pop("Дата премьеры", None)
            fields.pop("Источник даты", None)
    old = conn.execute("SELECT * FROM movies WHERE id=?", (fid,)).fetchone()
    now = now_utc()
    if old is not None:
        saved = json.loads(old["payload"])
        if ("Дата премьеры" in fields and saved.get("Дата премьеры") and
            source_rank(saved.get("Источник даты")) >
                source_rank(fields.get("Источник даты"))):
            fields.pop("Дата премьеры", None)
            fields.pop("Источник даты", None)
        if "Дата премьеры" not in fields:
            fields.pop("Источник даты", None)
        updated = False
        for key, value in fields.items():
            if key == "Название" and saved.get("Название") and "/premiere/" in str(source):
                continue
            if key in ("Ошибка", "Финальный URL карточки"):
                continue
            before = saved.get(key)
            if before is None or str(before) != str(value):
                conn.execute("""INSERT INTO changes
                    (movie_id, detected_at, field, old_value, new_value, source)
                    VALUES (?,?,?,?,?,?)""",
                    (fid, now, key, "" if before is None else str(before), str(value), source))
                saved[key] = value
                updated = True
        if updated:
            conn.execute("""UPDATE movies
                SET title=?,premiere=?,source=?,payload=?,updated_at=? WHERE id=?""",
                (saved.get("Название", ""), saved.get("Дата премьеры", ""),
                 source, json.dumps(saved, ensure_ascii=False), now, fid))
    else:
        if not fields.get("Название"):
            return False
        conn.execute("""INSERT INTO movies(id,title,premiere,source,payload,updated_at)
            VALUES (?,?,?,?,?,?)""",
            (fid, fields["Название"], fields.get("Дата премьеры", ""),
             source, json.dumps(fields, ensure_ascii=False), now))
        conn.execute("""INSERT INTO changes
            (movie_id, detected_at, field, old_value, new_value, source)
            VALUES (?,?,?,?,?,?)""",
            (fid, now, "Новый фильм", "", fields["Название"], source))
    conn.commit()
    return old is None



def log(conn, source, status, details, found=0):
    conn.execute("INSERT INTO runs(checked_at,source,status,details,found) VALUES(?,?,?,?,?)",
                 (now_utc(), str(source), str(status), str(details)[:900], int(found)))
    conn.commit()
