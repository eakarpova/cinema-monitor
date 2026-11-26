"""Read-only public metadata check: duplicate films and planned-to-watch numbers."""
import json,re,datetime
import requests
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from db import connect
from monitor import ROOT, HEADERS, get_page, extract_ssr_json
from kinobusiness import norm_title

conn=connect()
rows=[(r["id"],json.loads(r["payload"])) for r in conn.execute("SELECT id,payload FROM movies")]
print("DB MOVIES:",len(rows),flush=True)
for needle in ("ведьмин лес","щелкунчик"):
    print("=== DUPLICATES",needle,flush=True)
    for fid,film in rows:
        title=film.get("Название","")
        if needle in title.casefold():
            fields=("Название","Название из карточки","Оригинальное название","Оригинальное название Кинобизнес","Год производства","Год производства Кинобизнес","Дата премьеры","Дата премьеры Кинобизнес","Источник даты","Рейтинг Кинопоиска","Ждут премьеру","Количество оценок КП","Ссылка Кинобизнес","Ссылка")
            print(json.dumps({"id":fid,**{k:film.get(k) for k in fields}},ensure_ascii=False),flush=True)

top=sorted([(fid,p) for fid,p in rows if str(fid).isdigit()],key=lambda x:float(x[1].get("Ждут премьеру") or 0),reverse=True)[:12]
print("=== TOP WAIT COUNTS",flush=True)
for fid,film in top:
    print(fid,film.get("Название"),film.get("Ждут премьеру"),film.get("Дата премьеры"),film.get("Рейтинг Кинопоиска"),film.get("Количество оценок КП"),flush=True)
session=requests.Session()
session.headers.update(HEADERS)
for fid, film in top[:3]:
    try:
        html,url=get_page(session,f"{ROOT}/film/{fid}/")
        state=(extract_ssr_json(html) or {}).get("apolloState") or {}
        data=state.get("Film:"+fid) or {}
        rating=data.get("rating") or {}
        visible=BeautifulSoup(html,"html.parser").get_text(" ",strip=True)
        for word in ("Добавили в", "ждут премьеры","Буду смотреть","ожидают"):
            at=visible.find(word)
            if at>=0:print("VISIBLE",fid,word,visible[max(0,at-110):at+150],flush=True)
        print("LIVE",fid,film.get("Название"),"saved",film.get("Ждут премьеру"),"rating",json.dumps(rating,ensure_ascii=False)[:1800],"keys with wait",[(k,str(v)[:90]) for k,v in data.items() if any(q in k.lower() for q in ["wait","watch","anticip","expect","rating"])][:30],flush=True)
    except Exception as e:
        print("LIVE_ERROR",fid,type(e).__name__,str(e)[:250],flush=True)
conn.close()

print("=== FUZZY AND KB CANDIDATES",flush=True)
for fid,p in rows:
    fields=[p.get(k) or "" for k in ("Название","Название из карточки","Оригинальное название","Оригинальное название Кинобизнес")]
    forms=[norm_title(f) for f in fields]
    if (any("ведьмин" in f or "щелкунчик" in f for f in forms) or
        any(x in (p.get("Ссылка Кинобизнес") or "").lower() for x in ("vedm","nutcrack","shelkun","shchelk","witch"))):
        print("FUZZY",json.dumps({"id":fid,"titles":fields,"kp":p.get("Дата премьеры"),"kb":p.get("Дата премьеры Кинобизнес"),"year":p.get("Год производства"),"kb_year":p.get("Год производства Кинобизнес"),"url":p.get("Ссылка Кинобизнес")},ensure_ascii=False),flush=True)

for fid in ("5900225","9403071","9282370"):
    rec=next((x for x in rows if x[0]==fid),None)
    try:
        html,url=get_page(session,f"{ROOT}/film/{fid}/")
        state=(extract_ssr_json(html) or {}).get("apolloState") or {}
        data=state.get("Film:"+fid) or {}
        rating=data.get("rating") or {}
        text=BeautifulSoup(html,"html.parser").get_text(" ",strip=True)
        for word in ("Добавили в","ждут премьеры","Буду смотреть"):
            at=text.find(word)
            if at>=0:print("FUTURE_VISIBLE",fid,word,text[max(0,at-70):at+150],flush=True)
        print("FUTURE_LIVE",fid, rec[1].get("Название") if rec else "?", "saved",rec[1].get("Ждут премьеру") if rec else "?", "rating",json.dumps(rating,ensure_ascii=False)[:1700],flush=True)
    except Exception as e:
        print("FUTURE_ERROR",fid,type(e).__name__,str(e)[:150],flush=True)
