import datetime as dt
import unittest

import pandas as pd

from dashboard_logic import (
    applicable_events, filter_movies, leaders, likely_youth_theme,
    selected_any, target_month,
)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame([
            {
                "ID фильма": "111", "Название": "Семейный фильм",
                "Дата": pd.Timestamp("2027-09-15"), "Год фильтра": 2027,
                "Страна": "Россия, Франция", "Жанры": "комедия, семейный",
                "Прокатчик в России": "Прокатчик А",
                "Рейтинг Кинопоиска": "8.1", "Количество оценок КП": 150,
                "Ждут премьеру": 2500, "Возрастное ограничение": "6+",
                "Описание": "Приключения школьников",
            },
            {
                "ID фильма": "222", "Название": "Взрослый триллер",
                "Дата": pd.Timestamp("2027-10-01"), "Год фильтра": 2027,
                "Страна": "США", "Жанры": "триллер",
                "Прокатчик в России": "Прокатчик Б",
                "Рейтинг Кинопоиска": "9.0", "Количество оценок КП": 12,
                "Ждут премьеру": None, "Возрастное ограничение": "18+",
                "Описание": "",
            },
            {
                "ID фильма": "333", "Название": "Анонс без даты",
                "Дата": pd.NaT, "Год фильтра": 2030,
                "Страна": None, "Жанры": "мультфильм",
                "Прокатчик в России": None,
                "Рейтинг Кинопоиска": None, "Количество оценок КП": None,
                "Ждут премьеру": None, "Возрастное ограничение": "0+",
                "Описание": None,
            },
        ])

    def apply(self, **kwargs):
        defaults = {"years": [2027, 2030], "show_unknown": True}
        defaults.update(kwargs)
        return filter_movies(self.df, **defaults)

    def test_genre_filter_matches_multi_genre_movie(self):
        filtered = self.apply(chosen_genres=["комедия"])
        self.assertEqual(filtered["ID фильма"].tolist(), ["111"])
        self.assertTrue(selected_any("Комедия, семейный", ["комедия"]))

    def test_filters_restrict_catalog_and_values(self):
        filtered = self.apply(
            years=[2027],
            chosen_countries=["Франция"],
            chosen_distributors=["Прокатчик А"],
            rating_range=(8.0, 10.0),
            min_votes=100,
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered.iloc[0]["ID фильма"], "111")

    def test_undated_toggle_and_month_selection(self):
        self.assertEqual(len(self.apply(show_unknown=False)), 2)
        self.assertEqual(
            self.apply(date_range=(dt.date(2027, 9, 1), dt.date(2027, 9, 30)))
            ["ID фильма"].tolist(), ["111"])
        self.assertEqual(target_month(dt.date(2026, 11, 25), 10), (2027, 9))
        self.assertEqual(target_month(dt.date(2029, 12, 31), 1), (2030, 1))

    def test_events_use_filtered_ids(self):
        changes = pd.DataFrame([
            {"movie_id": "111", "field": "Новый фильм"},
            {"movie_id": "222", "field": "Дата премьеры"},
        ])
        filtered = self.apply(chosen_genres=["комедия"])
        self.assertEqual(applicable_events(changes, filtered)
                         ["movie_id"].tolist(), ["111"])

    def test_likely_youth_is_hint_not_exclusion(self):
        self.assertTrue(likely_youth_theme(self.df.iloc[0]))
        self.assertFalse(likely_youth_theme(self.df.iloc[1]))
        self.assertTrue(likely_youth_theme(self.df.iloc[2]))
        self.assertEqual(len(self.df), 3)

    def test_expected_premiere_uses_only_real_wait_counts(self):
        ranked = leaders(self.df)
        self.assertEqual(ranked["anticipated"]["ID фильма"], "111")
        self.assertEqual(ranked["top_rating"]["ID фильма"], "111")
        self.assertEqual(ranked["most_votes"]["ID фильма"], "111")
        no_data = self.df.iloc[[2]]
        self.assertEqual(leaders(no_data), {})


if __name__ == "__main__":
    unittest.main()
