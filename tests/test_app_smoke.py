"""Streamlit UI smoke test with the repository's saved premiere database."""
import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest


class CinemaAppSmokeTest(unittest.TestCase):
    def test_open_dashboard_and_filter_genre(self):
        path = str(Path(__file__).resolve().parents[1] / "app.py")
        app = AppTest.from_file(path, default_timeout=60).run()
        self.assertFalse(list(app.exception), [str(x.message) for x in app.exception])
        self.assertTrue(any("Кинопремьеры" in str(title.value) for title in app.title))
        genres = [widget for widget in app.multiselect if widget.label == "Жанры"]
        self.assertTrue(genres)
        self.assertTrue(list(app.dataframe), "Catalogue dataframe must render")
        before = len(app.dataframe[0].value)
        if "комедия" in genres[0].options:
            app = genres[0].set_value(["комедия"]).run()
            self.assertFalse(list(app.exception), [str(x.message) for x in app.exception])
            after = len(app.dataframe[0].value)
            self.assertLess(after, before, "Genre filter must narrow the catalogue")


if __name__ == "__main__":
    unittest.main()
