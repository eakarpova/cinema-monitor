"""Integration checks for the second public movie source and cross-source deduplication."""
import json
import sqlite3
import unittest

from db import upsert
from kinobusiness import (
    apply_record, match_target, parse_film_detail,
    parse_release_page, reconcile_kb_orphans, synthetic_id,
)
from dashboard_logic import likely_youth_theme


class KinobusinessSourceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE movies (
                id TEXT PRIMARY KEY, title TEXT, premiere TEXT, source TEXT,
                payload TEXT, updated_at TEXT);
            CREATE TABLE changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_id TEXT, detected_at TEXT, field TEXT,
                old_value TEXT, new_value TEXT, source TEXT);
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_at TEXT, source TEXT, status TEXT,
                details TEXT, found INTEGER DEFAULT 0);
        """)

    def tearDown(self):
        self.conn.close()

    def row(self, fid):
        row = self.conn.execute("SELECT payload FROM movies WHERE id=?", (fid,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def test_parse_actual_style_release_cards(self):
        html = """<select id="year"><option value="2026" selected>2026</option></select>
        <div class="sortable__grid">
          <div class="sortable__info">
            <div class="sortable__date"><span>26 ноября</span></div>
          </div>
          <div class="sortable__list">
            <div class="sortable__brick">
              <div class="sortable__picture"><img src="/poster.jpg"></div>
              <div class="sortable__item-description"><ul class="sortable__list-item">
                <li class="sortable__item name">
                  <h5><a class="fname" href="/movies/test-family/">Ум и Хрум: Сила пяти вкусов</a></h5>
                </li>
                <li class="sortable__item"><span>Страна: </span><span>Россия</span></li>
                <li class="sortable__item"><span>Жанр: </span><span itemprop="genre">мультфильм</span></li>
                <li class="sortable__item"><span>Год: </span><span>2026</span></li>
              </ul></div>
            </div>
          </div>
        </div>"""
        movies = parse_release_page(html, "https://www.kinobusiness.com/release/2026/11/")
        self.assertEqual(len(movies), 1)
        record = movies[0]
        self.assertEqual(record["Дата премьеры Кинобизнес"], "2026-11-26")
        self.assertEqual(record["Жанры Кинобизнес"], "мультфильм")
        self.assertEqual(record["Страна Кинобизнес"], "Россия")
        self.assertTrue(record["Постер Кинобизнес URL"].startswith("https://"))

    def test_detail_synopsis_and_release_date(self):
        html = """<meta name="description" content="Тест – 6+ – приключения">
        <h1 class="page__title">Ум и Хрум: Сила пяти вкусов</h1>
        <span itemprop="description"><h3>Краткое содержание:</h3>
         Два друга отправляются в необычное приключение и спасают мир.</span>
        <div>Дата выхода в России: 30.09.2027</div>"""
        detail = parse_film_detail(
            html, "https://www.kinobusiness.com/movies/um-i-khrum-sila-pyati-vkusov/")
        self.assertEqual(detail["Дата премьеры Кинобизнес"], "2027-09-30")
        self.assertEqual(detail["Возрастное ограничение Кинобизнес"], "6+")
        self.assertIn("приключение", detail["Описание Кинобизнес"])

    def test_merge_existing_kp_preserves_rating_and_fills_blanks(self):
        upsert(self.conn, {
            "id": "13508580", "Название": "Ум и Хрум. Сила пяти вкусов",
            "Год производства": 2027, "Дата премьеры": "2027-09-30",
            "Источник даты": "Календарь Кинопоиска: startDate",
            "Рейтинг Кинопоиска": 6.2,
        }, "https://www.kinopoisk.ru/film/13508580/")
        kb = {
            "Название": "Ум и Хрум: Сила пяти вкусов",
            "Год производства Кинобизнес": "2027",
            "Ссылка Кинобизнес": "https://www.kinobusiness.com/movies/um-i-khrum-sila-pyati-vkusov/",
            "Дата премьеры Кинобизнес": "2027-09-30",
            "Описание Кинобизнес": "Ум и Хрум ищут легендарные ингредиенты.",
            "Жанры Кинобизнес": "мультфильм",
        }
        self.assertEqual(apply_record(self.conn, kb), "matched")
        saved = self.row("13508580")
        self.assertEqual(saved["Рейтинг Кинопоиска"], 6.2)
        self.assertEqual(saved["Дата премьеры"], "2027-09-30")
        self.assertIn("ингредиенты", saved["Описание"])
        self.assertEqual(saved["Жанры"], "мультфильм")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0], 1)

    def test_unique_kb_only_movie_does_not_invent_kp_id(self):
        film = {
            "Название": "Тестовая киноновинка",
            "Дата премьеры Кинобизнес": "2028-03-14",
            "Год производства Кинобизнес": "2027",
            "Жанры Кинобизнес": "семейный",
            "Ссылка Кинобизнес": "https://www.kinobusiness.com/movies/new-premiere/",
        }
        self.assertEqual(apply_record(self.conn, film), "added")
        fid = synthetic_id(film["Ссылка Кинобизнес"])
        saved = self.row(fid)
        self.assertEqual(saved["Дата премьеры"], "2028-03-14")
        self.assertNotIn("ID фильма", saved)
        self.assertEqual(apply_record(self.conn, film), "matched")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0], 1)

    def test_ambiguous_title_is_not_added_again(self):
        upsert(self.conn, {"id": "11111", "Название": "Одинаковое название",
                           "Год производства": 2020, "Дата премьеры": "2026-01-01"},
               "https://www.kinopoisk.ru/film/11111/")
        film = {
            "Название": "Одинаковое название",
            "Год производства Кинобизнес": "2025",
            "Дата премьеры Кинобизнес": "2027-01-01",
            "Ссылка Кинобизнес": "https://www.kinobusiness.com/movies/ambiguous/",
        }
        self.assertEqual(apply_record(self.conn, film), "ambiguous")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0], 1)

    def test_kb_first_then_kp_reconciles_to_one_movie(self):
        film = {
            "Название": "Тестовая история",
            "Год производства Кинобизнес": "2027",
            "Дата премьеры Кинобизнес": "2027-09-30",
            "Ссылка Кинобизнес": "https://www.kinobusiness.com/movies/test-history/",
            "Описание Кинобизнес": "Подробный сюжет.",
        }
        apply_record(self.conn, film)
        upsert(self.conn, {"id": "12345", "Название": "Тестовая история",
                           "Год производства": 2027, "Рейтинг Кинопоиска": 7.5},
               "https://www.kinopoisk.ru/film/12345/")
        self.assertEqual(reconcile_kb_orphans(self.conn), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0], 1)
        self.assertEqual(self.row("12345")["Описание"], "Подробный сюжет.")

    def test_youth_franchise_even_without_genres(self):
        self.assertTrue(likely_youth_theme({
            "Название": "Ум и Хрум. Сила пяти вкусов", "Жанры": None,
            "Возрастное ограничение": None, "Описание": None,
        }))


if __name__ == "__main__":
    unittest.main()
