import json
import sqlite3
import unittest

from db import upsert
from monitor import parse_premiere_page, extract_ssr_json, extract_film_extra


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

    def test_calendar_cannot_downgrade_card_date(self):
        conn = self.database()
        upsert(conn, {"id": "8123456", "Название": "Настоящее название",
                      "Дата премьеры": "2028-10-12",
                      "Источник даты": "Календарь Кинопоиска: startDate"}, "https://www.kinopoisk.ru/premiere/ru/2028/month/10/")
        upsert(conn, {"id": "8123456", "Дата премьеры": "2028-11-01",
                      "Источник даты": "Карточка Кинопоиска: кинотеатральный релиз РФ"}, "https://www.kinopoisk.ru/film/8123456/")
        upsert(conn, {"id": "8123456", "Название": "Подробнее",
                      "Дата премьеры": "2028-10-12",
                      "Источник даты": "Календарь Кинопоиска: startDate"}, "https://www.kinopoisk.ru/premiere/ru/2028/month/10/")
        result = json.loads(conn.execute("SELECT payload FROM movies WHERE id='8123456'").fetchone()[0])
        self.assertEqual(result["Название"], "Настоящее название")
        self.assertEqual(result["Дата премьеры"], "2028-11-01")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM changes WHERE field='Новый фильм'").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
