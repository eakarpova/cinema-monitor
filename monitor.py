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
CARD_LIMIT = min(max(int(os.getenv("MAX_CARD_CHECKS", "50")), 0), 100)
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



def resolved_names(values, state, depth=0):
    """Genres/countries can be plain objects, Apollo refs or nested items."""
    if depth > 6 or values is None:
        return []
    if isinstance(values, str):
        name = clean(values)
        return [name] if name else []
    if isinstance(values, list):
        result = []
        for value in values:
            result.extend(resolved_names(value, state, depth + 1))
        return list(dict.fromkeys(result))
    if not isinstance(values, dict):
        return []
    if "__ref" in values:
        ref = values.get("__ref")
        return resolved_names(state.get(ref), state, depth + 1) if ref else []
    found = []
    for key in ("name", "displayName", "text", "title", "russian", "value"):
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            found.append(clean(value))
        elif isinstance(value, (dict, list)):
            found.extend(resolved_names(value, state, depth + 1))
    for key in ("items", "edges", "node", "genre", "genres", "countries"):
        if key in values:
            found.extend(resolved_names(values[key], state, depth + 1))
    return list(dict.fromkeys(found))


def safe_image_url(value):
    """Only public HTTPS Kinopoisk/Yandex poster hosts from page metadata."""
    if isinstance(value, str) and value.startswith("//"):
        value = "https:" + value
    if not isinstance(value, str) or not value.startswith("https://"):
        return ""
    host = (urlparse(value).hostname or "").lower()
    hosts = ("kinopoisk.ru", "yandex.net", "yandex.ru", "yandexusercontent.com")
    if not any(host == h or host.endswith("." + h) for h in hosts):
        return ""
    return value


def find_poster(value, state, depth=0):
    if depth > 6 or value is None:
        return ""
    if isinstance(value, str):
        return safe_image_url(value)
    if isinstance(value, list):
        for child in value:
            url = find_poster(child, state, depth + 1)
            if url:
                return url
    if isinstance(value, dict):
        ref = value.get("__ref")
        if isinstance(ref, str) and ref in state:
            return find_poster(state[ref], state, depth + 1)
        for key in ("url", "originalUrl", "urlPreview", "previewUrl",
                    "src", "imageUrl", "image", "poster", "items"):
            if key in value:
                url = find_poster(value[key], state, depth + 1)
                if url:
                    return url
    return ""


def page_metadata(html):
    """Poster and genre fallbacks from the actual film page, never guessed by ID."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for selector in ('meta[property="og:image"]', 'meta[name="twitter:image"]',
                     'link[rel="image_src"]'):
        tag = soup.select_one(selector)
        url = safe_image_url(tag.get("content") or tag.get("href")) if tag else ""
        if url:
            out["Постер URL"] = url
            break
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            node = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        if isinstance(node, list):
            items = node
        elif isinstance(node, dict):
            items = [node] + (node.get("@graph", []) if isinstance(node.get("@graph"), list) else [])
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            kinds = item.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if not any(k in ("Movie", "VideoObject") for k in kinds):
                continue
            genre = item.get("genre")
            if genre and not out.get("Жанры"):
                values = genre if isinstance(genre, list) else [genre]
                labels = [clean(v) for v in values if isinstance(v, str) and clean(v)]
                if labels:
                    out["Жанры"] = ", ".join(dict.fromkeys(labels))
            if not out.get("Постер URL"):
                img = item.get("image")
                url = safe_image_url(img) if isinstance(img, str) else ""
                if not url and isinstance(img, dict):
                    url = safe_image_url(img.get("url"))
                if url:
                    out["Постер URL"] = url
    if not out.get("Жанры"):
        tags = soup.select('meta[itemprop="genre"][content]')
        genres = [clean(t.get("content")) for t in tags if clean(t.get("content"))]
        if genres:
            out["Жанры"] = ", ".join(dict.fromkeys(genres))
    return out


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
    put("Жанры", ", ".join(resolved_names(film.get("genres"), state)))
    put("Постер URL", find_poster(film.get("poster") or film.get("posters") or film.get("images"), state))
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

        # Prioritize upcoming posters and missing genres, while still rotating
        # through the whole catalogue (including old imported records).
        all_rows = [(r["id"], json.loads(r["payload"])) for r in conn.execute(
            "SELECT id,payload FROM movies ORDER BY id")]
        today = dt.date.today()
        soon = []
        missing = []
        for fid, payload in all_rows:
            day = valid_date(payload.get("Дата премьеры"))
            date = dt.date.fromisoformat(day) if day else None
            if date and today <= date <= today + dt.timedelta(days=180):
                soon.append((date, fid))
            if not clean(payload.get("Жанры")) or not clean(payload.get("Постер URL")):
                missing.append((date or dt.date(2031, 1, 1), fid))
        soon.sort()
        missing.sort()
        to_check = []
        def include(ids, maximum):
            for fid in ids:
                if len(to_check) >= CARD_LIMIT or maximum <= 0:
                    return
                if fid not in to_check:
                    to_check.append(fid)
                    maximum -= 1
        include([fid for _, fid in soon], CARD_LIMIT // 3)
        include(new_ids, CARD_LIMIT // 3)
        include([fid for _, fid in missing], max(0, CARD_LIMIT - len(to_check) - 5))
        include(staggered([fid for fid, _ in all_rows], max(5, CARD_LIMIT)), CARD_LIMIT)
        cards_ok = cards_error = cards_genres = cards_posters = 0
        for fid in to_check:
            url = f"{ROOT}/film/{fid}/"
            try:
                html, _ = get_page(session, url)
                state = extract_ssr_json(html)
                if not state:
                    raise RuntimeError("SSR Apollo не найден")
                fields = extract_film_extra(state, fid)
                for key, value in page_metadata(html).items():
                    if value and not fields.get(key):
                        fields[key] = value
                if not fields:
                    raise RuntimeError("Данные фильма отсутствуют")
                fields["id"] = fid
                if fields.get("Название из карточки"):
                    fields["Название"] = fields["Название из карточки"]
                upsert(conn, fields, url)
                cards_ok += 1
                cards_genres += int(bool(fields.get("Жанры")))
                cards_posters += int(bool(fields.get("Постер URL")))
            except Exception as exc:
                cards_error += 1
                print(f"card {fid}: ERROR {exc}", file=sys.stderr, flush=True)
            time.sleep(DELAY + random.uniform(0, 0.2))

        summary = (
            f"monthly pages OK={pages_ok}, EMPTY={pages_empty}, ERROR={pages_error}; "
            f"found={len(discovered)} unique, new={added}, card OK={cards_ok}, "
            f"card errors={cards_error}, genres={cards_genres}, posters={cards_posters}, conflicts={conflicts}"
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
