"""Second public source for Russian theatrical premieres: Kinobusiness.

Only reads public pages, respects server errors, and never fabricates an
association between a Kinobusiness film and a Kinopoisk ID.
"""
import datetime as dt
import hashlib
import json
import os
import random
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from db import connect, log, upsert, valid_date, clear_kb_primary_dates

ROOT = "https://www.kinobusiness.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CinemaMonitorResearch/1.0; public-release-data)",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
DELAY = max(float(os.environ.get("KB_REQUEST_DELAY", "0.8")), 0.1)
DETAIL_LIMIT = max(0, min(70, int(os.environ.get("KB_DETAIL_LIMIT", "25"))))
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5,
    "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10,
    "ноября": 11, "декабря": 12,
}
# A relevant additional source for the explicitly requested family franchise.
EDITORIAL_REFERENCE_URLS = [ROOT + "/movies/um-i-khrum-sila-pyati-vkusov/"]


def tidy(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def norm_title(value):
    value = unicodedata.normalize("NFKC", tidy(value).casefold().replace("ё", "е"))
    return "".join(c for c in value if c.isalnum())


def safe_kb_url(value):
    if not value:
        return ""
    full = urljoin(ROOT, str(value))
    u = urlparse(full)
    if u.scheme not in ("https", "http"):
        return ""
    if (u.hostname or "").lower() not in ("kinobusiness.com", "www.kinobusiness.com"):
        return ""
    return u._replace(scheme="https", fragment="", query="").geturl()


def synthetic_id(url):
    return "kb_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def fetch_public(session, url):
    if not safe_kb_url(url):
        raise ValueError("Unexpected domain")
    response = session.get(url, timeout=25, allow_redirects=True)
    response.raise_for_status()
    if not safe_kb_url(response.url):
        raise RuntimeError("Redirected away from Kinobusiness")
    if any(token in response.text.lower() for token in
           ("captcha", "доступ ограничен", "access denied")):
        raise RuntimeError("Public page not accessible")
    return response.text


def parse_date_text(label, year):
    m = re.search(r"(\d{1,2})\s+([а-яё]+)", tidy(label).lower())
    if not m or m.group(2) not in MONTHS:
        return None
    try:
        return dt.date(int(year), MONTHS[m.group(2)], int(m.group(1))).isoformat()
    except ValueError:
        return None


def get_label(li):
    """Return labeled list item text without relying on internal class hashes."""
    spans = li.find_all("span", recursive=False)
    if spans:
        first = tidy(spans[0].get_text(" ", strip=True)).rstrip(":").casefold()
        if first in ("дистрибьютор", "страна", "жанр", "время", "год",
                     "режиссер", "в ролях", "бюджет"):
            return first, tidy(" ".join(s.get_text(" ", strip=True) for s in spans[1:]))
    text = tidy(li.get_text(" ", strip=True))
    if ":" in text:
        a, b = text.split(":", 1)
        return a.casefold().strip(), b.strip()
    return "", ""


def parse_release_page(html, url):
    """Each sortable__grid has a date heading plus its own film cards."""
    soup = BeautifulSoup(html, "html.parser")
    select = soup.select_one("select#year option[selected]")
    selected_year = int(select.get("value")) if select and str(select.get("value")).isdigit() else None
    match = re.search(r"/release/(\d{4})/(\d{1,2})/?", url)
    requested_year = int(match.group(1)) if match else selected_year
    if selected_year and requested_year and selected_year != requested_year:
        raise RuntimeError("The website returned a different year than requested")
    year = requested_year or selected_year
    if not year or not 2026 <= year <= 2030:
        raise RuntimeError("Release year not confirmed")
    output = []
    for grid in soup.select(".sortable__grid"):
        heading = grid.select_one(".sortable__info .sortable__date")
        date = parse_date_text(heading.get_text(" ", strip=True), year) if heading else None
        if not date:
            continue
        for brick in grid.select(".sortable__brick"):
            anchor = brick.select_one("a.fname[href*='/movies/']")
            if not anchor:
                continue
            href = safe_kb_url(anchor.get("href"))
            name = tidy(anchor.get_text(" ", strip=True))
            if not href or len(name) < 2:
                continue
            item = {
                "Название": name,
                "Ссылка Кинобизнес": href,
                "Дата премьеры Кинобизнес": date,
            }
            original = brick.select_one("[itemprop='alternativeHeadline']")
            if original:
                item["Оригинальное название Кинобизнес"] = tidy(original.get_text(" ", strip=True))
            picture = brick.select_one(".sortable__picture img[src]")
            if picture:
                image = safe_kb_url(picture.get("src"))
                if image:
                    item["Постер Кинобизнес URL"] = image
            for li in brick.select("li.sortable__item"):
                label, content = get_label(li)
                field = {
                    "дистрибьютор": "Прокатчик Кинобизнес",
                    "страна": "Страна Кинобизнес",
                    "жанр": "Жанры Кинобизнес",
                    "год": "Год производства Кинобизнес",
                    "режиссер": "Режиссер Кинобизнес",
                    "в ролях": "Актеры Кинобизнес",
                    "время": "Длительность Кинобизнес",
                }.get(label)
                if field and content and content != "-":
                    item[field] = content
            output.append(item)
    # Never claim a release when the site returned an unrecognized layout.
    if soup.select(".sortable__brick") and not output:
        raise RuntimeError("Film cards present, but dated groups could not be parsed")
    unique = {}
    for film in output:
        key = (film["Ссылка Кинобизнес"], film["Дата премьеры Кинобизнес"])
        unique[key] = film
    return list(unique.values())


def parse_film_detail(html, url):
    soup = BeautifulSoup(html, "html.parser")
    item = {"Ссылка Кинобизнес": safe_kb_url(url)}
    title = soup.select_one('h1[itemprop="name"], h1.film__title, h1.page__title')
    if title:
        name = tidy(title.get_text(" ", strip=True))
        if name:
            item["Название"] = re.sub(r"^Фильм\s*[–—-]\s*", "", name, flags=re.I)
    desc = soup.select_one('span[itemprop="description"], [itemprop="description"]')
    if desc:
        content = tidy(desc.get_text(" ", strip=True))
        content = re.sub(r"^Краткое содержание:\s*", "", content, flags=re.I)
        if len(content) >= 25:
            item["Описание Кинобизнес"] = content[:3000]
    if not item.get("Описание Кинобизнес"):
        desc_meta = soup.select_one('meta[property="og:description"]')
        if desc_meta:
            content = tidy(desc_meta.get("content"))
            if len(content) >= 25:
                item["Описание Кинобизнес"] = content[:3000]
    image = soup.select_one('meta[property="og:image"]')
    if image:
        poster = safe_kb_url(image.get("content"))
        if poster:
            item["Постер Кинобизнес URL"] = poster
    age = soup.select_one('meta[name="description"]')
    if age:
        match = re.search(r"(?<!\d)(0|6|12|16|18)\+", age.get("content") or "")
        if match:
            item["Возрастное ограничение Кинобизнес"] = match.group(0)
    full_text = soup.get_text(" ", strip=True)
    match = re.search(r"Дата выхода в России:\s*(\d{2})\.(\d{2})\.(20\d{2})", full_text)
    if match:
        iso = valid_date(f"{match.group(3)}-{match.group(2)}-{match.group(1)}")
        if iso:
            item["Дата премьеры Кинобизнес"] = iso
    genres = [tidy(x.get_text(" ", strip=True))
              for x in soup.select('[itemprop="genre"]')
              if tidy(x.get_text(" ", strip=True))]
    if genres:
        item["Жанры Кинобизнес"] = ", ".join(dict.fromkeys(genres))
    return item


def as_year(v):
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def match_target(conn, film):
    """Find an existing movie uniquely by title and supporting characteristics.

    Do not collapse a remake or same-name franchise entry without an extra
    characteristic. A near-collision goes into logs instead of a duplicate.
    """
    name = norm_title(film.get("Название"))
    original = norm_title(film.get("Оригинальное название Кинобизнес"))
    if not name:
        return None, False
    direct_url = film.get("Ссылка Кинобизнес", "")
    existing = []
    for row in conn.execute("SELECT id,payload FROM movies"):
        saved = json.loads(row["payload"])
        if direct_url and saved.get("Ссылка Кинобизнес") == direct_url:
            return row["id"], False
        existing.append((row["id"], saved))
    title_hits = []
    for fid, saved in existing:
        names = {
            norm_title(saved.get("Название")),
            norm_title(saved.get("Название из карточки")),
            norm_title(saved.get("Оригинальное название")),
            norm_title(saved.get("Оригинальное название Кинобизнес")),
        }
        names.discard("")
        if (name in names) or (original and original in names):
            title_hits.append((fid, saved))
    if not title_hits:
        return None, False
    yy = as_year(film.get("Год производства Кинобизнес"))
    date = film.get("Дата премьеры Кинобизнес")
    strong = []
    for fid, saved in title_hits:
        year = as_year(saved.get("Год производства") or saved.get("Год производства Кинобизнес"))
        when = saved.get("Дата премьеры") or saved.get("Дата премьеры Кинобизнес")
        year_match = yy is not None and year is not None and yy == year
        date_match = bool(date and when and date == when)
        if year_match or date_match:
            strong.append(fid)
    strong = list(dict.fromkeys(strong))
    if len(strong) == 1:
        return strong[0], False
    if strong:
        return None, True
    # A unique candidate can be linked if the production year is missing on
    # either source, and the verified release date differs by at most 45 days.
    if len(title_hits) == 1:
        fid, saved = title_hits[0]
        existing_year = as_year(saved.get("Год производства") or saved.get("Год производства Кинобизнес"))
        try:
            d1 = dt.date.fromisoformat(date) if date else None
            d2_raw = saved.get("Дата премьеры") or saved.get("Дата премьеры Кинобизнес")
            d2 = dt.date.fromisoformat(d2_raw) if d2_raw else None
        except ValueError:
            d1 = d2 = None
        if ((yy is None or existing_year is None) and
            d1 is not None and d2 is not None and abs((d1 - d2).days) <= 45):
            return fid, False
    return None, True


def combine_patch(film, saved, target_id):
    """Source-specific audit fields always added; core fields only fill blanks."""
    patch = {"id": target_id}
    patch["Ссылка Кинобизнес"] = film["Ссылка Кинобизнес"]
    for src in (
        "Дата премьеры Кинобизнес", "Оригинальное название Кинобизнес",
        "Постер Кинобизнес URL", "Прокатчик Кинобизнес", "Страна Кинобизнес",
        "Жанры Кинобизнес", "Год производства Кинобизнес",
        "Режиссер Кинобизнес", "Актеры Кинобизнес",
        "Длительность Кинобизнес", "Описание Кинобизнес",
        "Возрастное ограничение Кинобизнес",
    ):
        if film.get(src):
            patch[src] = film[src]
    main_fields = {
        "Описание Кинобизнес": "Описание",
        "Страна Кинобизнес": "Страна",
        "Жанры Кинобизнес": "Жанры",
        "Прокатчик Кинобизнес": "Прокатчик в России",
        "Постер Кинобизнес URL": "Постер URL",
        "Режиссер Кинобизнес": "Режиссер",
        "Актеры Кинобизнес": "Актеры",
        "Возрастное ограничение Кинобизнес": "Возрастное ограничение",
        "Год производства Кинобизнес": "Год производства",
    }
    for src, dest in main_fields.items():
        if film.get(src) and not saved.get(dest):
            patch[dest] = film[src]
    # For titles with a real Kinopoisk ID, the primary date is exclusively
    # Kinopoisk's verified calendar date. The secondary date remains visible
    # as "Дата премьеры Кинобизнес" but is never used as the main premiere.
    # Source-only kb_ films retain a clearly labelled provisional fallback.
    if (str(target_id).startswith("kb_") and
        film.get("Дата премьеры Кинобизнес") and not saved.get("Дата премьеры")):
        patch["Дата премьеры"] = film["Дата премьеры Кинобизнес"]
        patch["Источник даты"] = "Кинобизнес: предварительная дата российского проката"
    if not saved.get("Название"):
        patch["Название"] = film["Название"]
    if not saved.get("Ссылка"):
        # The primary link only falls back to Kinobusiness for source-only films.
        patch["Ссылка"] = film["Ссылка Кинобизнес"]
    return patch


def apply_record(conn, film, source="https://www.kinobusiness.com/release/"):
    """Merge or add; return action string. Preserve primary KP metrics and dates."""
    url = film.get("Ссылка Кинобизнес")
    if not url or not film.get("Название"):
        return "skip"
    existing_id, ambiguous = match_target(conn, film)
    if ambiguous:
        log(conn, url, "MATCH_REVIEW",
            "Совпадающее название без достаточных признаков: автоматическое объединение пропущено")
        return "ambiguous"
    if existing_id:
        row = conn.execute("SELECT payload FROM movies WHERE id=?", (existing_id,)).fetchone()
        saved = json.loads(row["payload"])
        patch = combine_patch(film, saved, existing_id)
        upsert(conn, patch, url)
        return "matched"
    fid = synthetic_id(url)
    patch = combine_patch(film, {}, fid)
    patch["Название"] = film["Название"]
    patch["Год календаря"] = film.get("Дата премьеры Кинобизнес", "")[:4]
    upsert(conn, patch, url)
    return "added"


def reconcile_kb_orphans(conn):
    """Merge prior source-only entries into newly discovered Kinopoisk IDs."""
    rows = list(conn.execute("SELECT id,payload FROM movies WHERE id LIKE 'kb_%'"))
    count = 0
    for row in rows:
        fid = row["id"]
        film = json.loads(row["payload"])
        match_id, ambiguous = match_target_excluding_self(conn, film, fid)
        if ambiguous or not match_id or not str(match_id).isdigit():
            continue
        primary = json.loads(conn.execute(
            "SELECT payload FROM movies WHERE id=?", (match_id,)).fetchone()["payload"])
        patch = combine_patch(film, primary, match_id)
        upsert(conn, patch, film.get("Ссылка Кинобизнес") or ROOT)
        # The canonical Kinopoisk movie persists. Link history to it.
        conn.execute("DELETE FROM changes WHERE movie_id=? AND field='Новый фильм'", (fid,))
        conn.execute("UPDATE changes SET movie_id=? WHERE movie_id=?", (match_id, fid))
        conn.execute("DELETE FROM movies WHERE id=?", (fid,))
        conn.commit()
        count += 1
    return count


def match_target_excluding_self(conn, film, excluded):
    # Avoid an exact-URL self-match by using a temporary minimal in-memory view
    # of competing candidates. Reuse the same conservative year/date criteria.
    rows = conn.execute("SELECT id,payload FROM movies WHERE id != ?", (excluded,)).fetchall()
    name = norm_title(film.get("Название"))
    orig = norm_title(film.get("Оригинальное название Кинобизнес"))
    if not name:
        return None, False
    yy = as_year(film.get("Год производства Кинобизнес"))
    date = film.get("Дата премьеры Кинобизнес")
    matches = []
    uncertain = []
    for row in rows:
        existing = json.loads(row["payload"])
        names = {norm_title(existing.get(k)) for k in (
            "Название", "Название из карточки", "Оригинальное название",
            "Оригинальное название Кинобизнес")}
        names.discard("")
        if name not in names and (not orig or orig not in names):
            continue
        uncertain.append(row["id"])
        year = as_year(existing.get("Год производства") or
                       existing.get("Год производства Кинобизнес"))
        when = existing.get("Дата премьеры") or existing.get("Дата премьеры Кинобизнес")
        years_agree = yy is not None and year is not None and yy == year
        days_agree = bool(date and when == date)
        if not years_agree and not days_agree and (yy is None or year is None):
            try:
                days_agree = abs((dt.date.fromisoformat(date) -
                                  dt.date.fromisoformat(when)).days) <= 45
            except (TypeError, ValueError):
                pass
        if years_agree or days_agree:
            matches.append(row["id"])
    return (matches[0], False) if len(matches) == 1 else (None, bool(uncertain))


def collect():
    conn = connect()
    session = requests.Session()
    session.headers.update(HEADERS)
    checked = matched = added = ambiguous = details_ok = errors = 0
    release_films = []
    try:
        # Read supported years from the public filter, rather than pretending
        # that a missing 2028 calendar is an empty release calendar.
        homepage = fetch_public(session, ROOT + "/release/")
        html = BeautifulSoup(homepage, "html.parser")
        valid_years = {
            int(opt.get("value"))
            for opt in html.select("select#year option[value]")
            if str(opt.get("value", "")).isdigit()
            and 2026 <= int(opt["value"]) <= 2030
        }
        if not valid_years:
            raise RuntimeError("Kinobusiness year filter was not found")
        for year in sorted(valid_years):
            for month in range(1, 13):
                url = f"{ROOT}/release/{year}/{month}/"
                try:
                    page = fetch_public(session, url)
                    films = parse_release_page(page, url)
                    checked += 1
                    log(conn, url, "OK" if films else "EMPTY",
                        f"Кинобизнес: {len(films)} записей", len(films))
                    release_films.extend(films)
                    print(f"KB {year}-{month:02d}: {len(films)}", flush=True)
                except Exception as exc:
                    errors += 1
                    log(conn, url, "ERROR", f"{type(exc).__name__}: {exc}")
                    print(f"KB {year}-{month:02d}: ERROR {exc}", flush=True)
                time.sleep(DELAY + random.uniform(0, 0.25))

        # Deduplicate the release index before updating the primary database.
        by_url = {}
        for film in release_films:
            by_url[film["Ссылка Кинобизнес"]] = film

        # The explicitly verified family feature is followed even if the
        # current site calendar doesn't include its future year in filters.
        for url in EDITORIAL_REFERENCE_URLS:
            by_url.setdefault(url, {"Название": "Ум и Хрум: Сила пяти вкусов",
                                    "Ссылка Кинобизнес": url})

        # Build richer profiles from selected details. Prioritize missing
        # descriptions and next 18 months of releases.
        today = dt.date.today()
        items = list(by_url.values())
        previous_details = {}
        for movie_row in conn.execute("SELECT payload FROM movies"):
            payload = json.loads(movie_row["payload"])
            if payload.get("Ссылка Кинобизнес"):
                previous_details[payload["Ссылка Кинобизнес"]] = payload
        def importance(film):
            title = norm_title(film.get("Название"))
            when = film.get("Дата премьеры Кинобизнес")
            try:
                d = dt.date.fromisoformat(when)
            except (TypeError, ValueError):
                d = dt.date(2031, 1, 1)
            near = today <= d <= today + dt.timedelta(days=540)
            is_family = any(word in title for word in ("умхрум", "мульт", "сказк", "детск"))
            # Rotate coverage: completed descriptions move behind films that
            # have not yet been checked, rather than scraping the same 25 daily.
            stored = previous_details.get(film["Ссылка Кинобизнес"], {})
            needs_description = not stored.get("Описание Кинобизнес")
            return (not is_family, not needs_description, not near, d)
        items.sort(key=importance)
        for film in items[:DETAIL_LIMIT]:
            try:
                extra = parse_film_detail(fetch_public(
                    session, film["Ссылка Кинобизнес"]), film["Ссылка Кинобизнес"])
                film.update({k: v for k, v in extra.items() if v})
                details_ok += 1
            except Exception as exc:
                log(conn, film["Ссылка Кинобизнес"], "DETAIL_ERROR",
                    f"{type(exc).__name__}: {exc}")
            time.sleep(DELAY + random.uniform(0, 0.25))

        for film in by_url.values():
            if not film.get("Дата премьеры Кинобизнес"):
                # A source-only film without a Russian theatrical release date
                # remains pending, not a fabricated premiere.
                log(conn, film.get("Ссылка Кинобизнес", ROOT), "UNDATED",
                    "Дата российского проката из Кинобизнеса не подтверждена")
                continue
            action = apply_record(conn, film)
            matched += int(action == "matched")
            added += int(action == "added")
            ambiguous += int(action == "ambiguous")
        reconciled = reconcile_kb_orphans(conn)
        corrected = clear_kb_primary_dates(conn)
        summary = (f"KB months OK={checked}, errors={errors}, listed={len(by_url)}, "
                   f"matched={matched}, new={added}, ambiguous={ambiguous}, "
                   f"details={details_ok}, reconciled={reconciled}, kp_date_only={corrected}")
        log(conn, "Kinobusiness release monitoring", "OK" if checked else "ERROR",
            summary, len(by_url))
        print(summary, flush=True)
        if not checked:
            raise SystemExit("No usable Kinobusiness monthly calendars; old data preserved")
    finally:
        session.close()
        conn.close()


if __name__ == "__main__":
    collect()
