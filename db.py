"""Database API. Compatible with the existing cinema.sqlite schema."""
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "cinema.sqlite"


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 15000")
    c.execute("""CREATE TABLE IF NOT EXISTS movies
        (id TEXT PRIMARY KEY, title TEXT, premiere TEXT, source TEXT, payload TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS changes
        (id INTEGER PRIMARY KEY AUTOINCREMENT, movie_id TEXT, detected_at TEXT,
         field TEXT, old_value TEXT, new_value TEXT, source TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS runs
        (id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT, source TEXT,
         status TEXT, details TEXT, found INTEGER DEFAULT 0)""")
    c.commit()
    return c


def now_utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def valid_date(value):
    s = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        date = dt.date.fromisoformat(s)
    except ValueError:
        return None
    return s if 2000 <= date.year <= 2100 else None


def date_rank(source):
    if "карточка" in str(source).lower():
        return 2
    if "календарь" in str(source).lower():
        return 1
    return 0


def upsert(c, film, source):
    """Merge only nonempty verified fields; preserve existing data on fetch failure.

    Calendar title never replaces a known title. Calendar date never downgrades
    a previously confirmed Russian cinema release date from a film card.
    """
    fid = str(film.get("id") or film.get("ID фильма") or "").strip()
    if not fid.isdigit():
        raise ValueError("Invalid Kinopoisk film ID")
    new_data = {k: v for k, v in film.items()
                if k != "id" and v is not None and v != "" and v != []}
    new_data["ID фильма"] = fid
    if "Дата премьеры" in new_data:
        clean = valid_date(new_data["Дата премьеры"])
        if clean:
            new_data["Дата премьеры"] = clean
        else:
            del new_data["Дата премьеры"]
            new_data.pop("Источник даты", None)

    old = c.execute("SELECT * FROM movies WHERE id=?", (fid,)).fetchone()
    now = now_utc()
    if old:
        data = json.loads(old["payload"])
        old_date_source = str(data.get("Источник даты", ""))
        new_date_source = str(new_data.get("Источник даты", ""))
        if ("Дата премьеры" in new_data
                and data.get("Дата премьеры")
                and date_rank(old_date_source) > date_rank(new_date_source)):
            new_data.pop("Дата премьеры", None)
            new_data.pop("Источник даты", None)
        if "Дата премьеры" not in new_data:
            new_data.pop("Источник даты", None)

        changed = False
        for key, value in new_data.items():
            if key == "Название" and data.get("Название") and "/premiere/" in source:
                continue
            if key in ("Ошибка", "Финальный URL карточки"):
                continue
            previous = data.get(key)
            # Do not record as a change when Python values are equivalent.
            if previous is None or str(previous) != str(value):
                c.execute("""INSERT INTO changes
                    (movie_id, detected_at, field, old_value, new_value, source)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (fid, now, key, "" if previous is None else str(previous), str(value), source))
                data[key] = value
                changed = True
        if changed:
            c.execute("""UPDATE movies SET title=?,premiere=?,source=?,payload=?,updated_at=?
                        WHERE id=?""", (data.get("Название", ""),
                         data.get("Дата премьеры", ""), source,
                         json.dumps(data, ensure_ascii=False), now, fid))
    else:
        if not new_data.get("Название"):
            # Never insert an unnamed row.
            return False
        c.execute("INSERT INTO movies VALUES (?,?,?,?,?,?)",
                  (fid, new_data["Название"], new_data.get("Дата премьеры", ""),
                   source, json.dumps(new_data, ensure_ascii=False), now))
        c.execute("""INSERT INTO changes
                    (movie_id,detected_at,field,old_value,new_value,source)
                    VALUES (?,?,?,?,?,?)""",
                  (fid, now, "Новый фильм", "", new_data["Название"], source))
    c.commit()
    return old is None


def log(c, source, status, details, found=0):
    c.execute("INSERT INTO runs(checked_at,source,status,details,found) VALUES (?,?,?,?,?)",
              (now_utc(), str(source), str(status), str(details)[:900], int(found)))
    c.commit()