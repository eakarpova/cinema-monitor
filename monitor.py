"""Daily Kinopoisk Russian cinema premieres monitor (2026–2030).

Based on the user's working notebook: monthly 'div.premier_item' records,
'meta[itemprop=startDate]', and the film-card SSR Apollo state.
No private cookies, no CAPTCHA bypass, no made-up dates.
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
from db import connect, upsert, log, valid_date

BASE_URL = "https://www.kinopoisk.ru"
YEARS = range(2026, 2031)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.kinopoisk.ru/premiere/ru/",
}
MAX_CARD_CHECKS = max(0, min(int(os.getenv("MAX_CARD_CHECKS", "25")), 100))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.5"))


def clean(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def get_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    # DO NOT paste personal browser session/cookies from the notebook here.
    return session


def fetch(session, url):
    response = session.get(url, timeout=30, allow_redirects=True)
    response.raise_for_status()
    final = response.url.lower()
    if urlparse(response.url).hostname not in ("kinopoisk.ru", "www.kinopoisk.ru"):
        raise RuntimeError("Unexpected redirect to another site")
    text = response.text
    if ("captcha" in final or "showcaptcha" in final or
        "sso.passport.yandex" in final or
        any(phrase in text.lower() for phrase in
            ("challenge-solver", "доступ ограничен", "access denied"))):
        raise RuntimeError("Site returned an access restriction or challenge")
    return text, response.url


def extract_id(item, name_link):
    raw_id = clean(item.get("id"))
    if raw_id.isdigit():
        return raw_id
    href = name_link.get("href", "") if name_link else ""
    match = re.search(r"/film/(\d+)(?:/|\?|$)", href)
    return match.group(1) if match else None


def parse_premiere_page(html, page_url):
    """Only entries inside actual premiere cards, never sidebar/trailer links."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select("div.premier_item"):
        name_link = (item.select_one('span.name[itemprop="name"] a')
                     or item.select_one("span.name a"))
        film_id = extract_id(item, name_link)
        title = clean(name_link.get_text(" ", strip=True)) if name_link else ""
        if not film_id or len(title) < 2:
            continue
        href = urljoin(BASE_URL, name_link.get("href", ""))
        if urlparse(href).hostname not in ("www.kinopoisk.ru", "kinopoisk.ru"):
            continue
        meta = item.select_one('meta[itemprop="startDate"]')
        raw_date = clean(meta.get("content", "")) if meta else ""
        date = valid_date(raw_date[:10]) if raw_date else None
        film = {
            "id": film_id,
            "ID фильма": film_id,
            "Название": title,
            "Ссылка": f"{BASE_URL}/film/{film_id}/",
            "Год календаря": re.search(r"/ru/(\d{4})/", page_url).group(1)
                              if re.search(r"/ru/(\d{4})/", page_url) else "",
        }
        if date:
            film["Дата премьеры"] = date
            film["Источник даты"] = "Календарь Кинопоиска: startDate"
        results.append(film)
    return results


def extract_ssr_json(html):
    """Robustly decode nested JSON after window.Ya.__ssr_initial_data = ."""
    match = re.search(r"window\.Ya\.__ssr_initial_data\s*=\s*", html)
    if not match:
        return None
    data = html[match.end():].lstrip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(data)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def ref_name(state, ref):
    entry = state.get(ref, {}) if ref else {}
    if not isinstance(entry, dict):
        return ""
    return clean(entry.get("displayName") or entry.get("name") or entry.get("originalName"))


def named_values(items):
    result = []
    for item in items or []:
        if isinstance(item, dict):
            value = clean(item.get("name") or item.get("text") or item.get("title"))
            if value:
                result.append(value)
    return ", ".join(dict.fromkeys(result))


def member_names(state, film, key):
    block = film.get(key) or {}
    names = []
    for item in block.get("items", []) or []:
        person = (item or {}).get("person") or {}
        name = ref_name(state, person.get("__ref"))
        if name:
            names.append(name)
    return ", ".join(dict.fromkeys(names))


def find_ru_cinema_release(film):
    """Notebook logic: countryId 2, CINEMA, rerelease=false."""
    distributions = film.get("distribution") or {}
    for key, block in distributions.items():
        compact = re.sub(r"\s+", "", str(key))
        if ('countryId":2' in compact and '"CINEMA"' in compact and
                '"rerelease":false' in compact):
            return block or {}
    return {}


def extract_film_extra(ssr_data, film_id):
    state = ssr_data.get("apolloState") or {}
    film = state.get(f"Film:{film_id}") or {}
    if not film:
        raise ValueError(f"Film:{film_id} absent from Apollo state")
    out = {}
    def put(key, value):
        if value is not None and value != "":
            out[key] = value

    title = film.get("title") or {}
    put("Название из карточки", clean(title.get("russian") or ""))
    put("Год производства", film.get("productionYear") or film.get("year"))
    put("Страна", named_values(film.get("countries")))
    put("Жанры", named_values(film.get("genres")))
    put("Описание", clean(film.get("synopsis")))
    put("Краткое описание", clean(film.get("shortDescription")))
    put("Слоган", clean(film.get("tagline")))
    put("Длительность, мин", film.get("duration"))
    age = (film.get("restriction") or {}).get("age") or ""
    put("Возрастное ограничение", {"age0":"0+", "age6":"6+",
        "age12":"12+", "age13":"13+", "age16":"16+", "age18":"18+"}.get(age, age))

    roles = {"Режиссер":'members({"limit":4,"role":"DIRECTOR"})',
             "Сценарий":'members({"limit":4,"role":"WRITER"})',
             "Продюсер":'members({"limit":4,"role":"PRODUCER"})',
             "Оператор":'members({"limit":4,"role":"OPERATOR"})',
             "Композитор":'members({"limit":4,"role":"COMPOSER"})',
             "Монтаж":'members({"limit":4,"role":"EDITOR"})',
             "Актеры":'members({"limit":10,"role":["ACTOR","CAMEO","UNCREDITED"]})'}
    for field, key in roles.items():
        put(field, member_names(state, film, key))

    rating = film.get("rating") or {}
    kp = rating.get("kinopoisk") or {}
    put("Рейтинг Кинопоиска", kp.get("value"))
    put("Количество оценок КП", kp.get("count"))
    put("Ждут премьеру", (rating.get("plannedToWatch") or {}).get("count"))

    release = find_ru_cinema_release(film)
    items = release.get("items") or []
    if items:
        item = items[0] or {}
        date = valid_date(str((item.get("date") or {}).get("date") or "")[:10])
        if date:
            put("Дата релиза РФ из карточки", date)
            out["Дата премьеры"] = date
            out["Источник даты"] = "Карточка Кинопоиска: кинотеатральный релиз РФ"
        companies = []
        for company in item.get("companies") or []:
            name = ref_name(state, (company or {}).get("__ref"))
            if name:
                companies.append(name)
        put("Прокатчик в России", ", ".join(dict.fromkeys(companies)))
    return out


def rotated(items, count):
    """Distribute periodic card refreshes across the catalogue."""
    if not items or count <= 0:
        return []
    k = (dt.date.today().toordinal() * count) % len(items)
    return (items[k:] + items[:k])[:count]


def run():
    conn = connect()
    session = get_session()
    discovered = {}
    page_success = 0
    page_failures = 0
    page_empty = 0
    changed_conflicts = 0
    disputed_ids = set()
    try:
        for year in YEARS:
            for month in range(1, 13):
                url = f"{BASE_URL}/premiere/ru/{year}/month/{month}/"
                try:
                    html, actual_url = fetch(session, url)
                    items = parse_premiere_page(html, url)
                    if not items:
                        # An empty month is possible, but 60 empty months indicate failure.
                        page_empty += 1
                        log(conn, url, "EMPTY", f"HTTP 200; no div.premier_item; final={actual_url}", 0)
                    else:
                        page_success += 1
                        log(conn, url, "OK", f"Premiere cards found; final={actual_url}", len(items))
                    for item in items:
                        fid = item["id"]
                        if fid not in discovered:
                            discovered[fid] = (item, url)
                        else:
                            prior = discovered[fid][0]
                            if (item.get("Дата премьеры") and prior.get("Дата премьеры")
                                    and item["Дата премьеры"] != prior["Дата премьеры"]):
                                changed_conflicts += 1
                                disputed_ids.add(fid)
                                log(conn, url, "CONFLICT", f"Film {fid}: contradictory calendar dates; left unchanged", 0)
                                # Wait for the card to resolve the date; never guess.
                                prior.pop("Дата премьеры", None)
                                prior.pop("Источник даты", None)
                            elif (fid not in disputed_ids and item.get("Дата премьеры")
                                  and not prior.get("Дата премьеры")):
                                prior["Дата премьеры"] = item["Дата премьеры"]
                                prior["Источник даты"] = item["Источник даты"]
                    print(f"{year}-{month:02d}: {len(items)} premiere cards")
                except Exception as exc:
                    page_failures += 1
                    log(conn, url, "ERROR", f"{type(exc).__name__}: {exc}")
                    print(f"{year}-{month:02d}: ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
                time.sleep(max(0.0, REQUEST_DELAY) + random.uniform(0, 0.3))

        new_ids = [fid for fid in discovered
                   if not conn.execute("SELECT 1 FROM movies WHERE id=?", (fid,)).fetchone()]
        inserted = 0
        for film, source in discovered.values():
            if upsert(conn, film, source):
                inserted += 1

        # Examine newly discovered cards first; rotate old catalogue across days.
        # Rotate across ALL existing films, not only cards still listed in the calendar.
        old_ids = [r["id"] for r in conn.execute("SELECT id FROM movies ORDER BY id")
                   if r["id"] not in new_ids]
        refresh_ids = new_ids[:MAX_CARD_CHECKS]
        refresh_ids += rotated(old_ids, MAX_CARD_CHECKS - len(refresh_ids))
        # When calendar fails, still try to refresh a few *existing* film cards.
        if not discovered and MAX_CARD_CHECKS:
            old_ids = [r["id"] for r in conn.execute("SELECT id FROM movies ORDER BY id")]
            refresh_ids = rotated(old_ids, MAX_CARD_CHECKS)

        card_ok = 0
        card_errors = 0
        for fid in refresh_ids:
            url = f"{BASE_URL}/film/{fid}/"
            try:
                html, final_url = fetch(session, url)
                ssr = extract_ssr_json(html)
                if ssr is None:
                    raise RuntimeError("window.Ya.__ssr_initial_data not found")
                extra = extract_film_extra(ssr, fid)
                if extra:
                    extra["id"] = fid
                    # Card title is more trustworthy than a sidebar link.
                    if extra.get("Название из карточки"):
                        extra["Название"] = extra["Название из карточки"]
                    upsert(conn, extra, url)
                card_ok += 1
            except Exception as exc:
                card_errors += 1
                print(f"CARD {fid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            time.sleep(max(0.0, REQUEST_DELAY) + random.uniform(0, 0.3))

        summary = (f"monthly pages OK={page_success}, EMPTY={page_empty}, ERROR={page_failures}; "
                   f"found={len(discovered)} unique, new={inserted}, card OK={card_ok}, "
                   f"card errors={card_errors}, calendar conflicts={changed_conflicts}")
        log(conn, "Kinopoisk monthly monitoring 2026–2030", "OK" if page_success else "ERROR", summary, len(discovered))
        print(summary)
        if page_success == 0:
            raise SystemExit("No Kinopoisk monthly calendar returned film cards; "
                             "new premieres could not be checked. Existing data was preserved.")
    finally:
        conn.close()
        session.close()


if __name__ == "__main__":
    run()