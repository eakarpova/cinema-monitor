"""Read-only comparison of saved dates with public Kinopoisk calendars."""
import json
import sqlite3
import requests
from monitor import ROOT, HEADERS, get_page, parse_premiere_page
from db import DB

IDS = ["5927239", "13299871", "10513206", "10064204", "628328"]
names = ["Голодные игры", "Дворняшка", "Пиратка", "Таро", "Ведьмин лес"]
print("=== CURRENT DB ===", flush=True)
with sqlite3.connect(DB) as conn:
    conn.row_factory = sqlite3.Row
    print("films:", conn.execute("select count(*) from movies").fetchone()[0], flush=True)
    for fid in IDS:
        row = conn.execute("select id,title,premiere,payload from movies where id=?", (fid,)).fetchone()
        if not row:
            print(fid, "MISSING", flush=True)
            continue
        p = json.loads(row["payload"])
        print(fid, row["title"], "premiere=", row["premiere"],
              "source_date=", p.get("Источник даты"), "card_date=", p.get("Дата релиза РФ из карточки"),
              flush=True)
        for ev in conn.execute("select field,old_value,new_value,source from changes where movie_id=? and field='Дата премьеры' order by id desc limit 4", (fid,)):
            print("  CHANGE", *ev, flush=True)
s = requests.Session()
s.headers.update(HEADERS)
for url in [ROOT + "/premiere/ru/", ROOT + "/premiere/ru/2026/month/11/"]:
    print("=== CALENDAR", url, flush=True)
    try:
        html, final = get_page(s, url)
        films = parse_premiere_page(html, url)
        print("final=",final, "cards=",len(films), "html_bytes=",len(html),flush=True)
        for f in films:
            if f["id"] in IDS or "Голодные игры" in f.get("Название",""):
                print(f["id"], f.get("Название"), f.get("Дата премьеры"), flush=True)
    except Exception as e:
        print("ERROR",type(e).__name__,str(e),flush=True)
