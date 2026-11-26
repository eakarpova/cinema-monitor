import json
import sqlite3
import unittest

from db import upsert
from monitor import parse_premiere_page, extract_ssr_json, extract_film_extra, resolved_names, page_metadata, safe_image_url, find_poster


class CinemaMonitorTests(unittest.TestCase):
    def database(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""CREATE TABLE movies (id TEXT PRIMARY KEY,title TEXT,premiere TEXT,source TEXT,payload TEXT,updated_at TEXT);
        CREATE TABLE changes (id INTEGER PRIMARY KEY AUTOINCREMENT,movie_id TEXT,detected_at TEXT,field TEXT,old_value TEXT,new_value TEXT,source TEXT);
        CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT,checked_at TEXT,source TEXT,status TEXT,details TEXT,found INTEGER DEFAULT 0);""")
        return conn

    def test_calendar_date_and_ignore_sidebar(self):
        html = """<div class="premier_item" id="8123456">
          <meta itemprop="startDate" content="2028-10-12"/>
          <span class="name" itemprop="name"><a href="/film/8123456/">Новая премьера</a></span>
          </div><a href="/film/9000000/">Боковая панель</a>"""
        found = parse_premiere_page(html, "https://www.kinopoisk.ru/premiere/ru/2028/month/10/")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["Дата премьеры"], "2028-10-12")
        self.assertEqual(found[0]["Год календаря"], "2028")

    def test_unknown_date_not_fabricated(self):
        html = '<div class="premier_item" id="8123456"><span class="name"><a href="/film/8123456/">Анонс</a></span></div>'
        film = parse_premiere_page(html, "https://www.kinopoisk.ru/premiere/ru/2030/month/2/")[0]
        self.assertNotIn("Дата премьеры", film)

    def test_planned_to_watch_count_records_verification_time(self):
        state = {"apolloState": {"Film:8123456": {
            "rating": {"plannedToWatch": {"count": 142965}}
        }}}
        fields = extract_film_extra(state, "8123456")
        self.assertEqual(fields["Ждут премьеру"], 142965)
        self.assertTrue(fields["Ждут премьеру проверено"].endswith("+00:00"))
        missing = extract_film_extra(
            {"apolloState": {"Film:8123456": {
                "rating": {"plannedToWatch": None}}}}, "8123456")
        self.assertNotIn("Ждут премьеру проверено", missing)
        self.assertNotIn("Ждут премьеру", missing)

    def test_ssr_card_russian_release(self):
        state = {"apolloState": {
            "Film:8123456": {"title": {"russian": "Премьера"}, "productionYear": 2027,
                "rating": {"kinopoisk": {"value": 7.2, "count": 100}},
                "distribution": {
                  'distribution({"countryId":2,"types":["CINEMA"],"rerelease":false})':
                  {"items": [{"date": {"date": "2028-11-01"},
                             "companies": [{"__ref": "Company:1"}]}]}}},
            "Company:1": {"displayName": "Прокатчик"}}}
        html = "<script>window.Ya.__ssr_initial_data = " + json.dumps(state, ensure_ascii=False) + ";</script>"
        extra = extract_film_extra(extract_ssr_json(html), "8123456")
        self.assertEqual(extra["Дата премьеры"], "2028-11-01")
        self.assertEqual(extra["Прокатчик в России"], "Прокатчик")


    def test_nested_apollo_genres_and_poster(self):
        state = {
            "Genre:1": {"name": "Комедия"},
            "Genre:2": {"displayName": "Фантастика"},
            "Poster:1": {"url": "https://avatars.mds.yandex.net/get-kinopoisk-image/test/orig"},
        }
        result = resolved_names(
            {"items": [{"genre": {"__ref": "Genre:1"}},
                       {"__ref": "Genre:2"}, {"name": "драма"}]}, state)
        self.assertEqual(result, ["Комедия", "Фантастика", "драма"])
        self.assertEqual(
            find_poster({"__ref": "Poster:1"}, state),
            "https://avatars.mds.yandex.net/get-kinopoisk-image/test/orig")

    def test_html_metadata_genre_and_poster(self):
        html = """<html><head>
        <meta property="og:image" content="https://avatars.mds.yandex.net/get-kinopoisk-image/test/300">
        <script type="application/ld+json">
        {"@type":"Movie","genre":["семейный","приключения"]}
        </script></head></html>"""
        fields = page_metadata(html)
        self.assertEqual(fields["Жанры"], "семейный, приключения")
        self.assertTrue(fields["Постер URL"].startswith("https://"))
        self.assertEqual(safe_image_url("https://example.com/cover.jpg"), "")

    def test_extract_genres_from_normalized_apollo(self):
        data = {"apolloState": {
            "Film:8123456": {
                "genres": {"items": [{"__ref": "Genre:1"}]},
                "poster": {"__ref": "Poster:1"},
            },
            "Genre:1": {"name": "мультфильм"},
            "Poster:1": {"urlPreview": "https://avatars.mds.yandex.net/get-kinopoisk-image/preview"}
        }}
        result = extract_film_extra(data, "8123456")
        self.assertEqual(result["Жанры"], "мультфильм")
        self.assertTrue(result["Постер URL"].startswith("https://"))


    def test_countries_from_apollo_refs(self):
        state = {"apolloState": {
            "Film:8123456": {
                "countries": {"items": [{"__ref": "Country:1"}, {"country": {"__ref": "Country:2"}}]}
            },
            "Country:1": {"name": "Россия"},
            "Country:2": {"displayName": "Казахстан"},
        }}
        result = extract_film_extra(state, "8123456")
        self.assertEqual(result["Страна"], "Россия, Казахстан")

    def test_countries_jsonld_fallback(self):
        html = """<script type="application/ld+json">
            {"@type": "Movie",
             "countryOfOrigin": [{"@type": "Country", "name": "Россия"},
                                 {"name": "Франция"}]}
            </script>"""
        fields = page_metadata(html)
        self.assertEqual(fields["Страна"], "Россия, Франция")

    def test_country_can_be_added_to_previously_empty_movie(self):
        conn = self.database()
        upsert(conn, {"id": "8123456", "Название": "Фильм"},
               "https://www.kinopoisk.ru/premiere/ru/2027/month/1/")
        upsert(conn, {"id": "8123456", "Страна": "Россия"},
               "https://www.kinopoisk.ru/film/8123456/")
        movie = json.loads(conn.execute(
            "SELECT payload FROM movies WHERE id='8123456'").fetchone()[0])
        self.assertEqual(movie["Страна"], "Россия")
        conn.close()


    def test_second_month_page_contains_current_releases(self):
        html = """<div class="premier_item" id="5927239">
        <meta itemprop="startDate" content="2026-11-26"/>
        <span class="name"><a href="/film/5927239/">
        Голодные игры: Рассвет Жатвы</a></span></div>"""
        row = parse_premiere_page(
            html, "https://www.kinopoisk.ru/premiere/ru/2026/month/11/?page=2")[0]
        self.assertEqual(row["Дата премьеры"], "2026-11-26")
        self.assertEqual(row["Год календаря"], "2026")

    def test_current_daily_schedule_overrides_stale_card_date(self):
        conn = self.database()
        fid = "5927239"
        upsert(conn, {"id": fid, "Название": "Голодные игры: Рассвет Жатвы",
                      "Дата премьеры": "2026-11-19",
                      "Источник даты": "Карточка Кинопоиска: кинотеатральный релиз РФ"},
               "https://www.kinopoisk.ru/film/5927239/")
        html = """<div class="premier_item" id="5927239">
        <meta itemprop="startDate" content="2026-11-26"/>
        <span class="name"><a href="/film/5927239/">
        Голодные игры: Рассвет Жатвы</a></span></div>"""
        url = "https://www.kinopoisk.ru/premiere/ru/date/2026-11-26/"
        row = parse_premiere_page(html, url)[0]
        self.assertEqual(row["Источник даты"],
                         "Календарь Кинопоиска: ежедневная проверка")
        upsert(conn, row, url)
        upsert(conn, {"id": fid, "Дата премьеры": "2026-11-19",
                      "Источник даты": "Карточка Кинопоиска: кинотеатральный релиз РФ"},
               "https://www.kinopoisk.ru/film/5927239/")
        data = json.loads(conn.execute(
            "SELECT payload FROM movies WHERE id=?", (fid,)).fetchone()[0])
        self.assertEqual(data["Дата премьеры"], "2026-11-26")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM changes WHERE movie_id=? AND field='Дата премьеры'",
            (fid,)).fetchone()[0], 1)
        conn.close()

    def test_calendar_schedule_overrides_stale_card(self):
        conn = self.database()
        upsert(conn, {"id": "8123456", "Название": "Настоящее название",
                      "Дата премьеры": "2028-11-01",
                      "Источник даты": "Карточка Кинопоиска: кинотеатральный релиз РФ"}, "https://www.kinopoisk.ru/film/8123456/")
        upsert(conn, {"id": "8123456", "Дата премьеры": "2028-10-12",
                      "Источник даты": "Календарь Кинопоиска: startDate"}, "https://www.kinopoisk.ru/premiere/ru/2028/month/10/")
        upsert(conn, {"id": "8123456", "Название": "Подробнее",
                      "Дата премьеры": "2028-10-12",
                      "Источник даты": "Календарь Кинопоиска: startDate"}, "https://www.kinopoisk.ru/premiere/ru/2028/month/10/")
        result = json.loads(conn.execute("SELECT payload FROM movies WHERE id='8123456'").fetchone()[0])
        self.assertEqual(result["Название"], "Настоящее название")
        self.assertEqual(result["Дата премьеры"], "2028-10-12")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM changes WHERE field='Новый фильм'").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
