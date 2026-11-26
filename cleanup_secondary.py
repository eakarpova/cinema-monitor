"""Remove retired Kinobusiness data, preserving Kinopoisk films and their history.

Idempotent; run from GitHub Actions before the Kinopoisk-only monitor.
No external requests. Changes to ordinary Kinopoisk fields are reversed only
when their latest recorded edit came from the retired secondary source.
"""
import json
import re
from collections import defaultdict

from db import connect, now_utc


def from_secondary(value):
    text = str(value or "").casefold()
    return ("kinobusiness" in text or "кинобизнес" in text)


def cleanup(conn, *, vacuum=True):
    rows = list(conn.execute("SELECT id, title, source, payload FROM movies"))
    changed = removed = stripped = 0

    for row in rows:
        fid = str(row["id"])
        if fid.startswith("kb_"):
            conn.execute("DELETE FROM movies WHERE id=?", (fid,))
            removed += 1
            continue

        # This is a real Kinopoisk identity; do not throw away the film.
        if not fid.isdigit():
            continue
        data = json.loads(row["payload"])
        original = dict(data)
        history = defaultdict(list)
        for event in conn.execute(
            "SELECT field, old_value, new_value, source FROM changes "
            "WHERE movie_id=? ORDER BY id", (fid,)
        ):
            history[event["field"]].append(event)

        # Undo a trailing chain of secondary-source changes, but never roll
        # back a more recent Kinopoisk change.
        for key, events in history.items():
            if not events or not from_secondary(events[-1]["source"]):
                continue
            index = len(events) - 1
            while index > 0 and from_secondary(events[index - 1]["source"]):
                index -= 1
            before = events[index]["old_value"]
            # Only touch the field if it still has the last recorded KB value.
            if key in data and str(data[key]) == str(events[-1]["new_value"]):
                if before is None or before == "" or before == "None":
                    data.pop(key, None)
                else:
                    data[key] = before

        for key in list(data):
            if from_secondary(key):
                data.pop(key, None)
        if from_secondary(data.get("Источник даты")):
            data.pop("Источник даты", None)
            data.pop("Дата премьеры", None)
        if from_secondary(data.get("Ссылка")):
            data["Ссылка"] = f"https://www.kinopoisk.ru/film/{fid}/"
        if from_secondary(data.get("Постер URL")):
            data.pop("Постер URL", None)
        if from_secondary(data.get("Источник")):
            data.pop("Источник", None)
        data["ID фильма"] = fid
        if data != original or from_secondary(row["source"]):
            stripped += len(set(original) - set(data))
            non_secondary_events = [
                e for events in history.values() for e in events
                if not from_secondary(e["source"])
            ]
            # Keep an original non-KB source where known. Fallback to the
            # canonical Kinopoisk film link, never a fictional scraped fact.
            source = row["source"]
            if from_secondary(source):
                source = (
                    non_secondary_events[-1]["source"] if non_secondary_events
                    else f"https://www.kinopoisk.ru/film/{fid}/"
                )
            conn.execute(
                "UPDATE movies SET title=?,premiere=?,source=?,payload=?,updated_at=? "
                "WHERE id=?",
                (
                    data.get("Название", row["title"]),
                    data.get("Дата премьеры") or "",
                    source,
                    json.dumps(data, ensure_ascii=False),
                    now_utc(),
                    fid,
                ),
            )
            changed += 1

    # Remove audit entries originating solely from the retired source.
    # Preserve Kinopoisk updates, including real date changes.
    events = list(conn.execute(
        "SELECT id, movie_id, field, source FROM changes"
    ))
    removed_events = 0
    for event in events:
        if (str(event["movie_id"]).startswith("kb_") or
                from_secondary(event["source"]) or
                from_secondary(event["field"])):
            conn.execute("DELETE FROM changes WHERE id=?", (event["id"],))
            removed_events += 1

    run_rows = list(conn.execute("SELECT id, source FROM runs"))
    removed_runs = 0
    for run in run_rows:
        if from_secondary(run["source"]):
            conn.execute("DELETE FROM runs WHERE id=?", (run["id"],))
            removed_runs += 1
    conn.commit()
    if vacuum:
        # Physical purge: the removed text is not retained in SQLite free pages.
        conn.execute("VACUUM")
        conn.commit()
    return {
        "secondary_only_movies_removed": removed,
        "kinopoisk_movies_cleaned": changed,
        "secondary_fields_removed": stripped,
        "secondary_events_removed": removed_events,
        "secondary_runs_removed": removed_runs,
        "remaining_movies": conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0],
    }


if __name__ == "__main__":
    with connect() as connection:
        stats = cleanup(connection)
    print("KINOBUSINESS CLEANUP:", json.dumps(stats, ensure_ascii=False), flush=True)
