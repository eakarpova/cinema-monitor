"""Pure filter and calendar helpers used by Cinema Monitor and its tests."""
import datetime as dt
import re
import pandas as pd

_FOCUS_GENRES = (
    "мультфильм", "анимац", "семейн", "детск", "аниме", "сказк",
    "фэнтези", "приключен", "подростков", "школьн"
)
_FOCUS_TEXT = (
    "школьник", "школа магии", "подрост", "юнош", "детств",
    "принцес", "фея", "феи", "волшебн", "сказоч"
)


def selected_any(value, selected):
    pieces = {x.strip().casefold() for x in re.split(r"[,;]", str(value or ""))}
    return bool(pieces.intersection(x.strip().casefold() for x in selected))


def filter_movies(df, *, years, show_unknown, search="", country="", genre="",
                  chosen_genres=(), chosen_countries=(), chosen_distributors=(),
                  rating_range=(0.0, 10.0), min_votes=0, date_range=(),
                  extra_col="Не выбран", extra_value=""):
    """The one filtered frame that every dashboard tab and export must use."""
    chosen_years = df["Год фильтра"].isin(years)
    missing_year = df["Год фильтра"].isna() & bool(show_unknown)
    out = df[chosen_years | missing_year].copy()
    if not show_unknown:
        out = out[out["Дата"].notna()]
    for col, query in [("Название", search), ("Страна", country), ("Жанры", genre)]:
        if str(query).strip():
            out = out[out[col].fillna("").astype(str).str.contains(
                str(query).strip(), case=False, regex=False)]
    for col, selected in [
        ("Жанры", chosen_genres), ("Страна", chosen_countries),
        ("Прокатчик в России", chosen_distributors),
    ]:
        if selected:
            out = out[out[col].apply(lambda v: selected_any(v, selected))]
    if tuple(rating_range) != (0.0, 10.0):
        rating = pd.to_numeric(out["Рейтинг Кинопоиска"], errors="coerce")
        out = out[rating.between(*rating_range)]
    if min_votes:
        votes = pd.to_numeric(out["Количество оценок КП"], errors="coerce")
        out = out[votes >= min_votes]
    if len(date_range) == 2:
        first, last = date_range
        if (first, last) != (dt.date(2026, 1, 1), dt.date(2030, 12, 31)):
            out = out[out["Дата"].dt.date.between(first, last)]
    if extra_col != "Не выбран" and str(extra_value).strip():
        if extra_col not in out.columns:
            return out.iloc[0:0].copy()
        out = out[out[extra_col].fillna("").astype(str).str.contains(
            str(extra_value).strip(), case=False, regex=False)]
    return out


def target_month(today, months_ahead):
    """A whole calendar month: Nov 2026 + 10 -> Sep 2027."""
    month_index = today.year * 12 + (today.month - 1) + int(months_ahead)
    year, month_zero = divmod(month_index, 12)
    return year, month_zero + 1


def likely_youth_theme(record):
    """Heuristic thematic hint, never a claim about an official target age."""
    if str(record.get("Возрастное ограничение") or "").strip() == "18+":
        return False
    genres = str(record.get("Жанры") or "").casefold()
    title = str(record.get("Название") or "").casefold()
    description = str(record.get("Краткое описание") or record.get("Описание") or "").casefold()
    return any(word in genres for word in _FOCUS_GENRES) or any(
        word in title or word in description for word in _FOCUS_TEXT)


def applicable_events(changes, movies):
    """Events always respect movie selections, unlike the old global table."""
    if changes.empty or movies.empty:
        return changes.iloc[0:0].copy()
    ids = set(movies["ID фильма"].dropna().map(lambda v: re.sub(r"\.0$", "", str(v))))
    return changes[changes["movie_id"].astype(str).isin(ids)].copy()


def numeric_values(movies, field):
    if field not in movies.columns:
        return pd.Series(float("nan"), index=movies.index)
    return pd.to_numeric(movies[field], errors="coerce")


def leaders(movies):
    """Rank only available metrics, never fill unknown values with invented zeros."""
    found = {}
    if movies.empty:
        return found
    for field, label in [
        ("Ждут премьеру", "anticipated"),
        ("Количество оценок КП", "most_votes"),
        ("Рейтинг Кинопоиска", "top_rating"),
    ]:
        numbers = numeric_values(movies, field)
        eligible = numbers.notna() & (numbers > 0)
        if field == "Рейтинг Кинопоиска":
            votes = numeric_values(movies, "Количество оценок КП")
            eligible = eligible & (votes >= 100)
        if eligible.any():
            idx = numbers[eligible].idxmax()
            found[label] = movies.loc[idx]
    return found
