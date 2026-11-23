import sqlite3, json, datetime
from pathlib import Path
DB=Path(__file__).resolve().parent/'data'/'cinema.sqlite'
def connect():
 DB.parent.mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 c.execute('CREATE TABLE IF NOT EXISTS movies (id TEXT PRIMARY KEY, title TEXT, premiere TEXT, source TEXT, payload TEXT, updated_at TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS changes (id INTEGER PRIMARY KEY AUTOINCREMENT, movie_id TEXT, detected_at TEXT, field TEXT, old_value TEXT, new_value TEXT, source TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT, source TEXT, status TEXT, details TEXT, found INTEGER DEFAULT 0)')
 c.commit();return c
def upsert(c, film, source):
 fid=str(film['id']); now=datetime.datetime.now(datetime.timezone.utc).isoformat()
 old=c.execute('SELECT * FROM movies WHERE id=?',(fid,)).fetchone()
 if old:
  data=json.loads(old['payload']); changed=False
  for k,v in film.items():
   if v is None or v=='':continue
   previous=data.get(k)
   # Не заменяем полное название случайным текстом ссылки из календаря.
   if k == 'Название' and str(source).startswith('https://www.kinopoisk.ru/premiere/') and previous:
    continue
   if previous != v:
    c.execute('INSERT INTO changes(movie_id,detected_at,field,old_value,new_value,source) VALUES(?,?,?,?,?,?)',(fid,now,k,str(previous or ''),str(v),source));data[k]=v;changed=True
  if changed:c.execute('UPDATE movies SET title=?,premiere=?,source=?,payload=?,updated_at=? WHERE id=?',(data.get('Название',''),data.get('Дата премьеры',''),source,json.dumps(data,ensure_ascii=False),now,fid))
 else:
  c.execute('INSERT INTO movies VALUES(?,?,?,?,?,?)',(fid,film.get('Название',''),film.get('Дата премьеры',''),source,json.dumps(film,ensure_ascii=False),now))
  c.execute('INSERT INTO changes(movie_id,detected_at,field,old_value,new_value,source) VALUES(?,?,?,?,?,?)',(fid,now,'Новый фильм','',film.get('Название',''),source))
 c.commit()
def log(c,source,status,details,found=0):
 c.execute('INSERT INTO runs(checked_at,source,status,details,found) VALUES(?,?,?,?,?)',(datetime.datetime.now(datetime.timezone.utc).isoformat(),source,status,details,found));c.commit()
