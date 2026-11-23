"""Мониторинг российских премьер Кинопоиска, 2026–2030.

Источник: помесячные страницы /premiere/ru/YEAR/month/MONTH/
и публичные данные карточек window.Ya.__ssr_initial_data.
Не используются cookies пользователя; блокировки не обходятся.
"""
import datetime as dt
import json
import os
import random
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from db import connect, log, upsert, valid_date

ROOT = "https://www.kinopoisk.ru"
YEARS = range(2026, 2031)
CARD_LIMIT = min(max(int(os.getenv("MAX_CARD_CHECKS", "25")), 0), 100)
DELAY = max(float(os.getenv("REQUEST_DELAY", "0.6")), 0.0)
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": ROOT + "/premiere/ru/",
}
BAD_TITLES = {"подробнее", "смотреть", "трейлер", "билеты", "купить билет", "о фильме"}


def clean(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def get_page(session, url):
    response = session.get(url, timeout=30, allow_redirects=True)
    response.raise_for_status()
    host = urlparse(response.url).hostname
    if host not in ("www.kinopoisk.ru", "kinopoisk.ru"):
        raise RuntimeError("Неожиданный переход на другой сайт")
    lower_url = response.url.lower()
    body = response.text
    if ("captcha" in lower_url or "showcaptcha" in lower_url or
        any(marker in body.lower() for marker in (
            "challenge-solver", "доступ ограничен", "access denied"))):
        raise RuntimeError("Кинопоиск ограничил автоматический доступ")
    return body, response.url


def parse_premiere_page(html, url):
    """Читаем только записи календаря, а не все ссылки страницы."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    year_match = re.search(r"/ru/(\d{4})/", url)
    for item in soup.select("div.premier_item"):
        link = item.select_one('span.name[itemprop="name"] a') or item.select_one("span.name a")
        if link is None:
            continue
        href = urljoin(ROOT, link.get("href", ""))
        film_id = clean(item.get("id"))
        if not film_id.isdigit():
            match = re.search(r"/film/(\d+)(?:/|$|\?)", href)
            film_id = match.group(1) if match else ""
        title = clean(link.get_text(" ", strip=True))
        if (not film_id.isdigit() or len(title) < 2 or len(title) > 200 or
            title.lower() in BAD_TITLES or
            urlparse(href).hostname not in ("www.kinopoisk.ru", "kinopoisk.ru")):
            continue
        record = {
            "id": film_id,
            "ID фильма": film_id,
            "Название": title,
            "Ссылка": f"{ROOT}/film/{film_id}/",
        }
        if year_match:
            record["Год календаря"] = year_match.group(1)
        date_meta = item.select_one('meta[itemprop="startDate"]')
        date_raw = clean(date_meta.get("content")) if date_meta else ""
        date_value = valid_date(date_raw[:10]) if date_raw else None
        if date_value:
            record["Дата премьеры"] = date_value
            record["Источник даты"] = "Календарь Кинопоиска: startDate"
        found.append(record)
    return found


def extract_ssr_json(html):
    marker = re.search(r"window\.Ya\.__ssr_initial_data\s*=\s*", html)
    if marker is None:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(html[marker.end():].lstrip())
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def ref_name(state, ref):
    item = state.get(ref) if ref else None
    if not isinstance(item, dict):
        return ""
    return clean(item.get("displayName") or item.get("name") or item.get("originalName"))


def names(values):
    found = []
    for value in values or []:
        if isinstance(value, dict):
            name = clean(value.get("name") or value.get("text") or value.get("title"))
            if name:
                found.append(name)
    return ", ".join(dict.fromkeys(found))


def persons(state, film, key):
    found = []
    data = film.get(key) or {}
    for item in data.get("items", []) or []:
        person = (item or {}).get("person") or {}
        name = ref_name(state, person.get("__ref"))
        if name:
            found.append(name)
    return ", ".join(dict.fromkeys(found))


def russian_release(film):
    """Только РФ (countryId=2), кинотеатры (CINEMA), не повторный прокат."""
    for key, block in (film.get("distribution") or {}).items():
        matched = re.search(r"\((\{.*\})\)$", str(key))
        if not matched:
            continue
        try:
            opts = json.loads(matched.group(1))
        except ValueError:
            continue
        if (opts.get("countryId") == 2 and
            "CINEMA" in (opts.get("types") or []) and
            opts.get("rerelease") is False):
            return block if isinstance(block, dict) else {}
    return {}


def extract_film_extra(ssr_data, film_id):
    state = (ssr_data or {}).get("apolloState") or {}
    film = state.get(f"Film:{film_id}") or {}
    if not film:
        raise ValueError(f"Нет Film:{film_id} в данных карточки")
    out = {}
    def put(field, value):
        if value is not None and value != "":
            out[field] = value

    title = film.get("title") or {}
    put("Название из карточки", clean(title.get("russian")))
    put("Год производства", film.get("productionYear") or film.get("year"))
    put("Страна", names(film.get("countries")))
    put("Жанры", names(film.get("genres")))
    put("Описание", clean(film.get("synopsis")))
    put("Краткое описание", clean(film.get("shortDescription")))
    put("Слоган", clean(film.get("tagline")))
    put("Длительность, мин", film.get("duration"))
    age = (film.get("restriction") or {}).get("age")
    put("Возрастное ограничение", {
        "age0": "0+", "age6": "6+", "age12": "12+",
        "age13": "13+", "age16": "16+", "age18": "18+"
    }.get(age, age))

    role_queries = {
        "Режиссер": 'members({"limit":4,"role":"DIRECTOR"})',
        "Сценарий": 'members({"limit":4,"role":"WRITER"})',
        "Продюсер": 'members({"limit":4,"role":"PRODUCER"})',
        "Оператор": 'members({"limit":4,"role":"OPERATOR"})',
        "Композитор": 'members({"limit":4,"role":"COMPOSER"})',
        "Монтаж": 'members({"limit":4,"role":"EDITOR"})',
        "Актеры": 'members({"limit":10,"role":["ACTOR","CAMEO","UNCREDITED"]})',
    }
    for field, query in role_queries.items():
        put(field, persons(state, film, query))
    rating = film.get("rating") or {}
    kp = rating.get("kinopoisk") or {}
    put("Рейтинг Кинопоиска", kp.get("value"))
    put("Количество оценок КП", kp.get("count"))
    put("Ждут премьеру", (rating.get("plannedToWatch") or {}).get("count"))

    items = russian_release(film).get("items") or []
    if items:
        item = items[0] or {}
        when = valid_date(str((item.get("date") or {}).get("date") or "")[:10])
        if when:
            out["Дата релиза РФ из карточки"] = when
            out["Дата премьеры"] = when
            out["Источник даты"] = "Карточка Кинопоиска: кинотеатральный релиз РФ"
        companies = []
        for entry in item.get("companies") or []:
            name = ref_name(state, (entry or {}).get("__ref"))
            if name:
                companies.append(name)
        put("Прокатчик в России", ", ".join(dict.fromkeys(companies)))
    return out


def staggered(ids, limit):
    if not ids or limit <= 0:
        return []
    offset = (dt.date.today().toordinal() * max(limit, 1)) % len(ids)
    return (ids[offset:] + ids[:offset])[:limit]


def run():
    conn = connect()
    session = requests.Session()
    session.headers.update(HEADERS)
    discovered = {}
    disputed = set()
    pages_ok = pages_empty = pages_error = 0
    conflicts = 0
    try:
        for year in YEARS:
            for month in range(1, 13):
                url = f"{ROOT}/premiere/ru/{year}/month/{month}/"
                try:
                    html, final_url = get_page(session, url)
                    films = parse_premiere_page(html, url)
                    if films:
                        pages_ok += 1
                        log(conn, url, "OK", f"Найдено карточек; URL={final_url}", len(films))
                    else:
                        pages_empty += 1
                        log(conn, url, "EMPTY", f"Нет div.premier_item; URL={final_url}")
                    for movie in films:
                        fid = movie["id"]
                        if fid not in discovered:
                            discovered[fid] = (movie, url)
                        else:
                            previous = discovered[fid][0]
                            a = previous.get("Дата премьеры")
                            b = movie.get("Дата премьеры")
                            if a and b and a != b:
                                conflicts += 1
                                disputed.add(fid)
                                previous.pop("Дата премьеры", None)
                                previous.pop("Источник даты", None)
                                log(conn, url, "CONFLICT", f"Противоречивые даты {fid}: {a} / {b}")
                            elif fid not in disputed and b and not a:
                                previous["Дата премьеры"] = b
                                previous["Источник даты"] = movie["Источник даты"]
                    print(f"calendar {year}-{month:02d}: {len(films)}", flush=True)
                except Exception as exc:
                    pages_error += 1
                    log(conn, url, "ERROR", f"{type(exc).__name__}: {exc}")
                    print(f"calendar {year}-{month:02d}: ERROR {exc}", file=sys.stderr, flush=True)
                time.sleep(DELAY + random.uniform(0, 0.2))

        new_ids = [fid for fid in discovered if not conn.execute(
            "SELECT 1 FROM movies WHERE id=?", (fid,)).fetchone()]
        added = 0
        for movie, url in discovered.values():
            if upsert(conn, movie, url):
                added += 1

        known = [r["id"] for r in conn.execute("SELECT id FROM movies ORDER BY id")
                 if r["id"] not in new_ids]
        to_check = new_ids[:CARD_LIMIT]
        to_check += staggered(known, CARD_LIMIT - len(to_check))
        if not discovered:
            known = [r["id"] for r in conn.execute("SELECT id FROM movies ORDER BY id")]
            to_check = staggered(known, CARD_LIMIT)
        cards_ok = cards_error = 0
        for fid in to_check:
            url = f"{ROOT}/film/{fid}/"
            try:
                html, _ = get_page(session, url)
                state = extract_ssr_json(html)
                if not state:
                    raise RuntimeError("SSR Apollo не найден")
                fields = extract_film_extra(state, fid)
                if not fields:
                    raise RuntimeError("Данные фильма отсутствуют")
                fields["id"] = fid
                if fields.get("Название из карточки"):
                    fields["Название"] = fields["Название из карточки"]
                upsert(conn, fields, url)
                cards_ok += 1
            except Exception as exc:
                cards_error += 1
                print(f"card {fid}: ERROR {exc}", file=sys.stderr, flush=True)
            time.sleep(DELAY + random.uniform(0, 0.2))

        summary = (
            f"monthly pages OK={pages_ok}, EMPTY={pages_empty}, ERROR={pages_error}; "
            f"found={len(discovered)} unique, new={added}, card OK={cards_ok}, "
            f"card errors={cards_error}, conflicts={conflicts}"
        )
        log(conn, "Kinopoisk monthly monitoring 2026–2030",
            "OK" if pages_ok else "ERROR", summary, len(discovered))
        print(summary, flush=True)
        if not pages_ok:
            raise SystemExit("Не удалось получить записи ни одного месячного календаря; "
                             "предыдущая база сохранена.")
    finally:
        conn.close()
        session.close()


if __name__ == "__main__":
    run()
