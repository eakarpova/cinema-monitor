import json
import sqlite3
import unittest

from cleanup_secondary import cleanup


class RemoveSecondarySourceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
          CREATE TABLE movies (id TEXT PRIMARY KEY, title TEXT, premiere TEXT,
             source TEXT, payload TEXT, updated_at TEXT);
          CREATE TABLE changes (id INTEGER PRIMARY KEY AUTOINCREMENT, movie_id TEXT,
             detected_at TEXT, field TEXT, old_value TEXT, new_value TEXT, source TEXT);
          CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT,
             source TEXT, status TEXT, details TEXT, found INTEGER DEFAULT 0);
        """)

    def tearDown(self):
        self.conn.close()

    def insert(self, fid, value, source):
        self.conn.execute(
            "INSERT INTO movies VALUES (?,?,?,?,?,?)",
            (fid, value["Название"], value.get("Дата премьеры", ""),
             source, json.dumps(value, ensure_ascii=False), "t0"),
        )

    def event(self, fid, field, old, new, source):
        self.conn.execute(
            "INSERT INTO changes(movie_id,detected_at,field,old_value,new_value,source) "
            "VALUES (?,?,?,?,?,?)", (fid, "t0", field, old, new, source)
        )

    def get(self, fid):
        row = self.conn.execute("SELECT payload FROM movies WHERE id=?", (fid,)).fetchone()
        return json.loads(row[0]) if row else None

    def test_remove_kb_only_and_secondary_enrichment_keep_kp(self):
        kp = "https://www.kinopoisk.ru/film/628328/"
        kb = "https://www.kinobusiness.com/movies/vedmin-les/"
        self.insert("628328", {
            "Название": "Ведьмин лес", "Дата премьеры": "2026-12-03",
            "Источник даты": "Календарь Кинопоиска: startDate",
            "Рейтинг Кинопоиска": 8.229,
            "Количество оценок КП": 675,
            "Описание": "Сюжет с Кинобизнеса",
            "Жанры": "мультфильм",
            "Ссылка Кинобизнес": kb,
            "Постер URL": "https://www.kinobusiness.com/image.jpg",
        }, kb)
        self.insert("kb_1234567890abcdef", {
            "Название": "Ведьмин лес Wildwood",
            "Дата премьеры": "2026-12-03",
            "Ссылка Кинобизнес": kb,
        }, kb)
        self.event("628328", "Описание", "", "Сюжет с Кинобизнеса", kb)
        self.event("628328", "Жанры", "фэнтези", "мультфильм", kb)
        self.event("628328", "Дата премьеры", "2026-12-01", "2026-12-03", kp)
        self.event("kb_1234567890abcdef", "Новый фильм", "",
                   "Ведьмин лес Wildwood", kb)
        self.conn.execute(
            "INSERT INTO runs (source,status,details) VALUES (?,?,?)",
            ("Kinobusiness release monitoring", "OK", "test")
        )
        result = cleanup(self.conn, vacuum=False)
        self.assertEqual(result["secondary_only_movies_removed"], 1)
        self.assertEqual(result["remaining_movies"], 1)
        record = self.get("628328")
        self.assertEqual(record["Описание"] if "Описание" in record else None, None)
        self.assertEqual(record["Жанры"], "фэнтези")
        self.assertEqual(record["Дата премьеры"], "2026-12-03")
        self.assertEqual(record["Рейтинг Кинопоиска"], 8.229)
        self.assertEqual(record["Количество оценок КП"], 675)
        self.assertEqual(record["ID фильма"], "628328")
        self.assertNotIn("Ссылка Кинобизнес", record)
        self.assertNotIn("Постер URL", record)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
        self.assertEqual(cleanup(self.conn, vacuum=False)["secondary_only_movies_removed"], 0)

    def test_keep_newer_kinopoisk_fields_and_original_movies(self):
        kp = "https://www.kinopoisk.ru/film/324206/"
        kb = "https://www.kinobusiness.com/movies/nutcracker/"
        self.insert("324206", {
            "Название": "Щелкунчик и Крысиный король",
            "Жанры": "семейный",
            "Постер URL": "https://avatars.mds.yandex.net/pic",
            "ID фильма": "324206",
        }, kp)
        self.event("324206", "Жанры", "", "комедия", kb)
        self.event("324206", "Жанры", "комедия", "семейный", kp)
        self.event("324206", "Постер URL", "", "https://avatars.mds.yandex.net/pic", kp)
        result = cleanup(self.conn, vacuum=False)
        self.assertEqual(result["remaining_movies"], 1)
        self.assertEqual(self.get("324206")["Жанры"], "семейный")
        self.assertIn("Постер URL", self.get("324206"))

    def test_clear_legacy_secondary_primary_date_without_inventing_kp_date(self):
        kb = "https://www.kinobusiness.com/movies/test/"
        self.insert("12345", {
            "Название": "Фильм", "Дата премьеры": "2027-01-01",
            "Источник даты": "Кинобизнес: предварительная дата",
            "Ссылка": kb,
        }, kb)
        result = cleanup(self.conn, vacuum=False)
        self.assertEqual(result["remaining_movies"], 1)
        record = self.get("12345")
        self.assertNotIn("Дата премьеры", record)
        self.assertNotIn("Источник даты", record)
        self.assertEqual(record["Ссылка"], "https://www.kinopoisk.ru/film/12345/")


if __name__ == "__main__":
    unittest.main()
