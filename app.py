"""Cinema Monitor — interactive Streamlit dashboard, backed by cinema.sqlite."""
import json
import math
import html
import hashlib
from io import BytesIO
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
import re
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st
from db import connect
from dashboard_logic import (filter_movies, applicable_events, likely_youth_theme,
                             leaders, numeric_values, target_month)

st.set_page_config(page_title="Кинопремьеры России", page_icon="🎬", layout="wide")
st.title("🎬 Кинопремьеры России · 2026–2030")
st.caption("Каталог российских кинопремьер · Кинопоиск · мониторинг изменений")

with connect() as conn:
    rows = conn.execute("SELECT * FROM movies").fetchall()
    changes = pd.read_sql_query("SELECT * FROM changes ORDER BY detected_at DESC", conn)
    runs = pd.read_sql_query("SELECT * FROM runs ORDER BY checked_at DESC", conn)

if not rows:
    st.warning("База пуста. Сначала загрузите исходные фильмы с помощью import_seed.py.")
    st.stop()

df = pd.DataFrame([json.loads(row["payload"]) for row in rows])
df["_movie_id"] = [str(row["id"]) for row in rows]
fields = ["Название", "Дата премьеры", "Год календаря", "Год производства",
          "Страна", "Жанры", "Прокатчик в России", "Рейтинг Кинопоиска",
          "Количество оценок КП", "Ждут премьеру", "Возрастное ограничение",
          "Описание", "Краткое описание", "Режиссер", "Актеры", "Продюсер",
          "Постер URL", "ID фильма", "Ссылка", "Источник даты",
          "Ссылка Кинобизнес", "Дата премьеры Кинобизнес",
          "Описание Кинобизнес", "Жанры Кинобизнес",
          "Прокатчик Кинобизнес", "Страна Кинобизнес",
          "Возрастное ограничение Кинобизнес"]
for col in fields:
    if col not in df.columns:
        df[col] = None

df["Дата"] = pd.to_datetime(df["Дата премьеры"], errors="coerce")
df["Год премьеры"] = df["Дата"].dt.year
# Show undated films in the year where they appeared in the Kinopoisk calendar.
df["Год фильтра"] = df["Год премьеры"].fillna(
    pd.to_numeric(df["Год календаря"], errors="coerce"))

st.sidebar.header("Фильтры")
years = st.sidebar.multiselect("Год премьеры", list(range(2026, 2031)),
                               default=list(range(2026, 2031)))
search = st.sidebar.text_input("Поиск по названию")
country = st.sidebar.text_input("Страна содержит")
genre = st.sidebar.text_input("Жанр содержит")
show_unknown = st.sidebar.checkbox("Показывать анонсы без точной даты", value=True)



def options_from_column(column):
    values = set()
    for raw in df[column].dropna().astype(str):
        values.update(x.strip() for x in re.split(r"[,;]", raw) if x.strip())
    return sorted(values, key=str.casefold)


with st.sidebar.expander("🔎 Фильтры по столбцам", expanded=True):
    chosen_genres = st.multiselect("Жанры", options_from_column("Жанры"),
                                   placeholder="Любой жанр")
    chosen_countries = st.multiselect("Страны", options_from_column("Страна"),
                                      placeholder="Любая страна")
    chosen_distributors = st.multiselect(
        "Прокатчики", options_from_column("Прокатчик в России"),
        placeholder="Любой прокатчик")
    rating_range = st.slider("Рейтинг Кинопоиска", 0.0, 10.0,
                              (0.0, 10.0), step=0.1)
    min_votes = st.number_input("Минимум оценок КП", min_value=0,
                                 value=0, step=1000)
    date_range = st.date_input("Дата премьеры — от и до",
                               value=(dt.date(2026, 1, 1), dt.date(2030, 12, 31)),
                               min_value=dt.date(2026, 1, 1),
                               max_value=dt.date(2030, 12, 31))
    st.caption("Можно одновременно выбирать несколько значений каждого поля.")
    searchable_columns = [
        "Название", "ID фильма", "Год производства", "Режиссер", "Актеры",
        "Описание", "Страна", "Жанры", "Прокатчик в России", "Источник даты"
    ]
    extra_col = st.selectbox("Дополнительный фильтр: столбец",
                             ["Не выбран"] + searchable_columns)
    extra_value = st.text_input("Столбец содержит", placeholder="Введите значение")


# This is the single source of truth for catalog, charts, cards and exports.
f = filter_movies(
    df, years=years, show_unknown=show_unknown, search=search,
    country=country, genre=genre, chosen_genres=chosen_genres,
    chosen_countries=chosen_countries,
    chosen_distributors=chosen_distributors,
    rating_range=rating_range, min_votes=min_votes,
    date_range=date_range, extra_col=extra_col, extra_value=extra_value,
)
st.sidebar.caption(f"По выбранным фильтрам: {len(f)} из {len(df)} фильмов.")
f_ids = f["_movie_id"].astype(str)
filtered_changes = applicable_events(changes, f)

recent = pd.DataFrame()
if not filtered_changes.empty:
    filtered_changes["when"] = pd.to_datetime(
        filtered_changes["detected_at"], utc=True, errors="coerce")
    recent = filtered_changes[
        filtered_changes["when"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)]
    # Initial import has source 'Исходный Excel'. It is not a new premiere.
    new7 = int(((recent["field"] == "Новый фильм") &
                (~recent["source"].fillna("").str.contains("Исходный Excel", case=False, regex=False))).sum())
    shift7 = int(((recent["field"] == "Дата премьеры") &
                  (recent["old_value"].fillna("").str.strip() != "")).sum())
else:
    new7 = 0
    shift7 = 0

summary_runs = (runs[runs["source"].fillna("").str.contains("Kinopoisk monthly monitoring", case=False)]
                if not runs.empty else pd.DataFrame())
if not summary_runs.empty:
    latest = summary_runs.iloc[0]
    stamp = pd.to_datetime(latest["checked_at"], utc=True, errors="coerce")
    stamp_text = stamp.strftime("%d.%m.%Y %H:%M UTC") if pd.notna(stamp) else "неизвестно"
    if latest["status"] == "OK":
        st.info(f"Последний запуск мониторинга: {stamp_text} · {latest['details']}")
    else:
        st.warning(f"Последний мониторинг завершился без подтверждённых данных: {stamp_text}. "
                   "Каталог сохранён без удаления старых фильмов. Подробности в журнале проверок.")
else:
    st.warning("Автоматический мониторинг ещё не проходил успешную проверку. "
               "Зайдите в GitHub → Actions → Daily premiere check.")

metric_a, metric_b, metric_c, metric_d = st.columns(4)
metric_a.metric("Фильмов в выборке", len(f))
metric_b.metric("Новых за 7 дней в выборке", new7)
metric_c.metric("Переносов дат в выборке", shift7)
metric_d.metric("Без точной даты", int(f["Дата"].isna().sum()))

st.caption("Часть дат исходной выгрузки представлена первым числом месяца. "
           "Для точного планирования проката проверяйте поле «Источник даты» и карточку фильма.")


today_msk = dt.datetime.now(ZoneInfo("Europe/Moscow")).date()
RU_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря")


def available_text(value):
    if value is None or pd.isna(value):
        return ""
    result = str(value).strip()
    return "" if result.lower() in ("nan", "none", "null") else result


def pretty_date(movie):
    raw_date = pd.to_datetime(movie.get("Дата премьеры"), errors="coerce")
    if pd.isna(raw_date):
        cal_year = available_text(movie.get("Год календаря"))
        return f"Дата уточняется · календарь {cal_year}" if cal_year else "Дата уточняется"
    day = raw_date.date()
    source = available_text(movie.get("Источник даты"))
    if day.day == 1 and "Кинопоиска" not in source:
        return f"{RU_MONTHS[day.month - 1].capitalize()} {day.year} · день уточняется"
    return f"{day.day} {RU_MONTHS[day.month - 1]} {day.year}"


def card(movie, *, tag="", note=""):
    with st.container(border=True):
        if tag:
            st.markdown(f"**{tag}**")
        poster = available_text(movie.get("Постер URL"))
        if poster.startswith("https://"):
            st.image(poster, use_container_width=True)
        else:
            st.markdown(
                '<div style="height:180px;display:flex;align-items:center;'
                'justify-content:center;border-radius:10px;'
                'background:#28324c;color:#ffffff">🎬 Постер уточняется</div>',
                unsafe_allow_html=True)
        title = available_text(movie.get("Название")) or "Без названия"
        st.markdown("**" + html.escape(title) + "**")
        st.caption("📅 " + pretty_date(movie))
        genre_text = available_text(movie.get("Жанры"))
        if genre_text:
            st.caption("🎭 " + genre_text[:140])
        rating = pd.to_numeric(movie.get("Рейтинг Кинопоиска"), errors="coerce")
        votes = pd.to_numeric(movie.get("Количество оценок КП"), errors="coerce")
        watchers = pd.to_numeric(movie.get("Ждут премьеру"), errors="coerce")
        rating_text = f"⭐ {rating:.1f}/10" if pd.notna(rating) and rating > 0 else "⭐ Рейтинг пока отсутствует"
        st.write(rating_text)
        if pd.notna(votes) and votes > 0:
            st.caption(f"👥 Оценок КП: {int(votes):,}".replace(",", " "))
        if pd.notna(watchers) and watchers > 0:
            st.caption(f"🔥 Ждут премьеру: {int(watchers):,}".replace(",", " "))
        age = available_text(movie.get("Возрастное ограничение"))
        if age:
            st.caption("Возрастное ограничение: " + age)
        if likely_youth_theme(movie):
            st.caption("🎈 Возможный интерес для детской / подростковой аудитории")
        if note:
            st.info(note)
        short = available_text(movie.get("Краткое описание")) or available_text(movie.get("Описание"))
        if short:
            with st.expander("О фильме"):
                st.write(short[:1200])
                cast = available_text(movie.get("Актеры"))
                director = available_text(movie.get("Режиссер"))
                if cast:
                    st.caption("Актёры: " + cast[:240])
                if director:
                    st.caption("Режиссёр: " + director[:180])
        link = available_text(movie.get("Ссылка"))
        if not link.startswith("https://www.kinopoisk.ru/film/"):
            fid = available_text(movie.get("ID фильма"))
            if fid.isdigit():
                link = "https://www.kinopoisk.ru/film/" + fid + "/"
        if link.startswith("https://www.kinopoisk.ru/film/"):
            st.link_button("Открыть на Кинопоиске", link, use_container_width=True)
        kb_link = available_text(movie.get("Ссылка Кинобизнес"))
        if kb_link.startswith("https://www.kinobusiness.com/movies/"):
            st.link_button("Карточка Кинобизнес", kb_link, use_container_width=True)
        kb_date = available_text(movie.get("Дата премьеры Кинобизнес"))
        main_date = available_text(movie.get("Дата премьеры"))
        if kb_date and main_date and kb_date != main_date:
            st.caption(f"⚠️ Даты источников различаются: "
                       f"Кинопоиск/база {main_date}, Кинобизнес {kb_date}")


def card_grid(frame, *, limit=None, labels=None, notes=None):
    if frame.empty:
        st.info("По текущим фильтрам фильмы не найдены.")
        return
    shown = frame.head(limit) if limit is not None else frame
    recs = shown.to_dict("records")
    for start in range(0, len(recs), 3):
        cols = st.columns(3, gap="medium")
        for column, movie in zip(cols, recs[start:start + 3]):
            fid = re.sub(r"\.0$", "", available_text(movie.get("ID фильма")))
            with column:
                card(movie, tag=(labels or {}).get(fid, ""),
                     note=(notes or {}).get(fid, ""))


upcoming = f[f["Дата"].notna()].copy()
upcoming = upcoming[upcoming["Дата"].dt.date >= today_msk].sort_values(
    ["Дата", "Название"], na_position="last")
ranked = leaders(upcoming)
highlight_labels = {}
for key, label in (
    ("anticipated", "🔥 ОЖИДАЕМАЯ ПРЕМЬЕРА"),
    ("top_rating", "⭐ ВЫСОКИЙ РЕЙТИНГ"),
    ("most_votes", "👥 БОЛЬШЕ ВСЕГО ОЦЕНОК"),
):
    if key in ranked:
        fid = re.sub(r"\.0$", "", available_text(ranked[key].get("ID фильма")))
        if fid and fid not in highlight_labels:
            highlight_labels[fid] = label

st.subheader("🎞️ Ближайшие премьеры")
st.caption("Все карточки и таблицы ниже используют одинаковые фильтры слева. "
           "Даты, указанные первым числом без подтверждённого дня, могут обозначать только месяц.")
card_grid(upcoming, limit=9, labels=highlight_labels)

if "anticipated" in ranked:
    st.subheader("🔥 Ожидаемая премьера")
    st.caption("Фильм с наибольшим доступным показателем «Ждут премьеру» в текущей будущей выборке.")
    highlight = ranked["anticipated"].to_frame().T
    card_grid(highlight, labels=highlight_labels)

tab_gallery, tab_months, tab_catalog, tab_overview, tab_events, tab_logs = st.tabs([
    "🎬 Все премьеры", "🗓 Через N месяцев", "🎞 Каталог",
    "📊 Аналитика", "🔔 Новинки и переносы", "⚙️ Проверки источников",
])

with tab_gallery:
    st.subheader("Все предстоящие премьеры")
    current = f[(f["Дата"].isna()) | (f["Дата"].dt.date >= today_msk)].copy()
    if not current.empty:
        opt1, opt2, opt3 = st.columns([2, 2, 1])
        with opt1:
            ordering = st.selectbox("Сортировать", [
                "По ближайшей дате", "Сначала возможные детские и подростковые сюжеты",
                "По ожиданиям", "По количеству оценок", "По рейтингу",
            ], key="all_premieres_sort")
        with opt2:
            page_size = st.selectbox("Карточек на странице", [12, 24, 48], index=1,
                                     key="all_premieres_per_page")
        with opt3:
            focus_only = st.checkbox("Только тематический фокус", value=False,
                                     key="all_premieres_focus")
        current["_focus"] = current.apply(likely_youth_theme, axis=1)
        if focus_only:
            current = current[current["_focus"]].copy()
        if ordering == "Сначала возможные детские и подростковые сюжеты":
            current = current.sort_values(["_focus", "Дата", "Название"],
                                          ascending=[False, True, True],
                                          na_position="last")
        elif ordering == "По ожиданиям":
            current["_sort"] = numeric_values(current, "Ждут премьеру")
            current = current.sort_values(["_sort", "Дата"], ascending=[False, True],
                                          na_position="last")
        elif ordering == "По количеству оценок":
            current["_sort"] = numeric_values(current, "Количество оценок КП")
            current = current.sort_values(["_sort", "Дата"], ascending=[False, True],
                                          na_position="last")
        elif ordering == "По рейтингу":
            current["_sort"] = numeric_values(current, "Рейтинг Кинопоиска")
            current = current.sort_values(["_sort", "Дата"], ascending=[False, True],
                                          na_position="last")
        else:
            current = current.sort_values(["Дата", "Название"], na_position="last")
        pages = max(1, math.ceil(len(current) / page_size))
        if st.session_state.get("premiere_page", 1) > pages:
            st.session_state["premiere_page"] = 1
        page = st.number_input("Страница", min_value=1, max_value=pages,
                                value=1, step=1, key="premiere_page")
        st.caption(f"Найдено {len(current)} фильмов · страница {page} из {pages}. "
                   "Анонсы без точной даты идут после датированных премьер.")
        card_grid(current.iloc[(page - 1) * page_size:page * page_size],
                  labels=highlight_labels)

with tab_months:
    st.subheader("Какие фильмы выйдут через N месяцев?")
    month_ahead = st.number_input("Через сколько месяцев", min_value=0,
                                  max_value=49, value=10, step=1,
                                  key="months_ahead")
    year, month = target_month(today_msk, month_ahead)
    st.markdown(f"### {RU_MONTHS[month - 1].capitalize()} {year}")
    st.caption("Показывается весь календарный месяц, отсчитанный от текущего месяца. "
               "Например, ноябрь 2026 + 10 месяцев = сентябрь 2027.")
    period_movies = f[(f["Дата"].dt.year == year) & (f["Дата"].dt.month == month)].copy()
    period_movies = period_movies.sort_values(["Дата", "Название"])
    st.metric("Премьер в выбранном месяце", len(period_movies))
    if period_movies.empty:
        st.info("В этом месяце по выбранным фильтрам пока нет премьер с известной датой.")
    else:
        interest = period_movies[period_movies.apply(likely_youth_theme, axis=1)]
        other = period_movies[~period_movies.index.isin(interest.index)]
        if not interest.empty:
            st.subheader("🎈 Возможные детские и подростковые сюжеты")
            st.caption("Автоматическая подсказка по жанрам и описаниям. "
                       "Возрастную направленность лучше проверять по карточке фильма.")
            card_grid(interest, labels=highlight_labels)
        if not other.empty:
            st.subheader("Остальные премьеры месяца")
            card_grid(other, labels=highlight_labels)
    st.caption("Никакие жанры не исключены из базы: этот раздел просто группирует фильмы.")

with tab_overview:
    left, right = st.columns(2)
    with left:
        st.subheader("Премьеры по годам")
        counts = (f.dropna(subset=["Год фильтра"])
                  .groupby("Год фильтра").size().reset_index(name="Фильмов"))
        if not counts.empty:
            counts["Год фильтра"] = counts["Год фильтра"].astype(int).astype(str)
            plot = px.bar(counts, x="Год фильтра", y="Фильмов", text="Фильмов",
                          category_orders={"Год фильтра": [str(y) for y in range(2026, 2031)]})
            plot.update_layout(xaxis_type="category", xaxis_title="Год")
            st.plotly_chart(plot, use_container_width=True)
        else:
            st.info("Нет фильмов для построения графика")
    with right:
        st.subheader("По месяцам")
        monthly = (f.dropna(subset=["Дата"])
                   .assign(Месяц=lambda x: x["Дата"].dt.to_period("M").astype(str))
                   .groupby("Месяц").size().reset_index(name="Фильмов"))
        if not monthly.empty:
            plot = px.line(monthly, x="Месяц", y="Фильмов", markers=True)
            plot.update_layout(xaxis_type="category")
            st.plotly_chart(plot, use_container_width=True)
        else:
            st.info("Нет дат для построения графика")
    st.subheader("Распределение премьер по жанрам")
    st.caption("Один фильм может относиться к нескольким жанрам. "
               "В таком случае он учитывается в каждом из них.")
    raw_genres = (f["Жанры"].fillna("").astype(str)
                  .str.split(r"[,;]").explode().str.strip())
    genres_clean = raw_genres[raw_genres.ne("") &
                              ~raw_genres.str.lower().isin(["none", "nan", "null"])]
    if not genres_clean.empty:
        genres_clean = genres_clean.str.casefold()
        genre_counts = (genres_clean.value_counts().head(20)
                        .rename_axis("Жанр").reset_index(name="Фильмов"))
        genre_counts["Жанр"] = genre_counts["Жанр"].str.capitalize()
        chart = px.bar(genre_counts, x="Фильмов", y="Жанр",
                       orientation="h", text="Фильмов",
                       category_orders={"Жанр": genre_counts["Жанр"].iloc[::-1].tolist()})
        chart.update_layout(height=max(360, len(genre_counts) * 30),
                            xaxis_title="Количество фильмов",
                            yaxis_title="", yaxis_type="category")
        st.plotly_chart(chart, use_container_width=True)
    else:
        st.info("Для текущей выборки жанры ещё не получены. "
                "Они заполняются при ежедневной проверке карточек.")


def export_excel(data):
    """Formatted XLSX; respects the active catalogue filters and shown columns."""
    output = BytesIO()
    sheet_data = data.copy().reset_index(drop=True)

    # True Excel values rather than text, for date / numeric filters and sorting.
    if "Дата премьеры" in sheet_data.columns:
        sheet_data["Дата премьеры"] = pd.to_datetime(
            sheet_data["Дата премьеры"], errors="coerce").dt.date
    for key in ("Рейтинг Кинопоиска", "Количество оценок КП", "Ждут премьеру"):
        if key in sheet_data.columns:
            sheet_data[key] = pd.to_numeric(sheet_data[key], errors="coerce")
    if "ID фильма" in sheet_data.columns:
        sheet_data["ID фильма"] = sheet_data["ID фильма"].apply(
            lambda v: str(v) if pd.notna(v) else "")

    # Treat externally scraped text as literal data, not Excel formulas.
    for key in sheet_data.select_dtypes(include=["object"]).columns:
        sheet_data[key] = sheet_data[key].map(
            lambda v: ("'" + v) if isinstance(v, str) and
            v.lstrip().startswith(("=", "+", "-", "@")) else v)

    with pd.ExcelWriter(output, engine="openpyxl",
                        date_format="DD.MM.YYYY", datetime_format="DD.MM.YYYY") as writer:
        sheet_data.to_excel(writer, sheet_name="Кинопремьеры", index=False)
        ws = writer.sheets["Кинопремьеры"]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        ws.sheet_view.showGridLines = False
        header_fill = PatternFill("solid", fgColor="20324C")
        stripe_fill = PatternFill("solid", fgColor="F1F5FA")
        ws.row_dimensions[1].height = 29

        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            cell.alignment = Alignment(vertical="center", wrap_text=True)

        for row in ws.iter_rows(min_row=2):
            ws.row_dimensions[row[0].row].height = 20
            for cell in row:
                if cell.row % 2 == 0:
                    cell.fill = stripe_fill
                cell.alignment = Alignment(vertical="center")
                if sheet_data.columns[cell.column - 1] == "Дата премьеры":
                    cell.number_format = "DD.MM.YYYY"
                if sheet_data.columns[cell.column - 1] == "ID фильма":
                    cell.number_format = "@"
                if (sheet_data.columns[cell.column - 1] == "Ссылка" and
                    isinstance(cell.value, str) and
                    cell.value.startswith("https://www.kinopoisk.ru/film/")):
                    cell.hyperlink = cell.value
                    cell.style = "Hyperlink"

        column_widths = {
            "Дата премьеры": 17, "Название": 43, "Год производства": 20,
            "Страна": 23, "Жанры": 34, "Прокатчик в России": 32,
            "Рейтинг Кинопоиска": 23, "Количество оценок КП": 25,
            "ID фильма": 17, "Режиссер": 32, "Актеры": 42,
            "Ждут премьеру": 22, "Краткое описание": 52,
            "Описание": 58, "Возрастное ограничение": 24,
            "Источник даты": 45, "Ссылка": 47, "Постер URL": 48,
        }
        for index, name in enumerate(sheet_data.columns, 1):
            ws.column_dimensions[get_column_letter(index)].width = column_widths.get(name, 24)
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.print_options.horizontalCentered = True
        ws.print_title_rows = "1:1"
    return output.getvalue()


with tab_catalog:
    st.subheader("Полный каталог премьер")
    st.caption("В каталоге отображаются только фильмы, прошедшие выбранные фильтры. "
               "При изменении фильтров обновляются и таблица, и Excel, и карточки.")
    st.metric("Фильмов после фильтрации", len(f))
    columns = [
        "Дата премьеры", "Название", "Год производства", "Страна", "Жанры",
        "Возрастное ограничение", "Прокатчик в России", "Рейтинг Кинопоиска",
        "Количество оценок КП", "Ждут премьеру", "Режиссер", "Актеры",
        "Продюсер", "Краткое описание", "Описание", "Постер URL",
        "ID фильма", "Источник даты", "Год календаря", "Ссылка",
        "Ссылка Кинобизнес", "Дата премьеры Кинобизнес",
        "Описание Кинобизнес", "Жанры Кинобизнес",
        "Прокатчик Кинобизнес", "Страна Кинобизнес",
    ]
    defaults = [
        "Дата премьеры", "Название", "Год производства", "Страна", "Жанры",
        "Возрастное ограничение", "Прокатчик в России",
        "Рейтинг Кинопоиска", "Количество оценок КП", "Ждут премьеру",
        "ID фильма", "Источник даты", "Ссылка", "Ссылка Кинобизнес",
    ]
    visible_columns = st.multiselect(
        "Какие столбцы показывать", options=columns,
        default=defaults, key="catalog_columns",
    )
    if visible_columns:
        selected = f.sort_values(["Дата", "Название"], na_position="last")[
            visible_columns].copy()
        # Normalize mixed values from source JSON, so Streamlit and Excel use
        # stable column types and the table redraws correctly after filtering.
        for numeric_col in ("Рейтинг Кинопоиска", "Количество оценок КП",
                            "Ждут премьеру"):
            if numeric_col in selected.columns:
                selected[numeric_col] = pd.to_numeric(
                    selected[numeric_col], errors="coerce")
        if "Дата премьеры" in selected.columns:
            selected["Дата премьеры"] = pd.to_datetime(
                selected["Дата премьеры"], errors="coerce")
        for text_col in selected.columns.difference(
                ["Рейтинг Кинопоиска", "Количество оценок КП",
                 "Ждут премьеру", "Дата премьеры", "Дата премьеры Кинобизнес"]):
            selected[text_col] = selected[text_col].map(
                lambda v: None if v is None or
                (isinstance(v, float) and pd.isna(v)) else str(v))
        # Recreate the grid on each changed selection to prevent stale client-side
        # column sorting/selection from confusing filtered catalogue results.
        fingerprint = hashlib.sha1(
            (",".join(sorted(f_ids.tolist())) + "|" +
             ",".join(visible_columns)).encode("utf-8")).hexdigest()[:14]
        st.dataframe(
            selected, key="catalogue_" + fingerprint,
            use_container_width=True, hide_index=True, height=560,
            column_config={
                "Ссылка": st.column_config.LinkColumn("Кинопоиск"),
                "Ссылка Кинобизнес": st.column_config.LinkColumn("Кинобизнес"),
                "Постер URL": st.column_config.ImageColumn("Постер"),
                "Ждут премьеру": st.column_config.NumberColumn("Ждут премьеру"),
            })
        st.caption(f"Для выгрузки выбрано: {len(selected)} строк, "
                   f"{len(selected.columns)} столбцов. Файл учитывает фильтры слева.")
        xlsx_col, csv_col = st.columns(2)
        with xlsx_col:
            st.download_button(
                "📗 Скачать Excel (.xlsx)",
                data=export_excel(selected),
                file_name="cinema_premieres_filtered.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="download_xlsx",
            )
        with csv_col:
            st.download_button(
                "Скачать CSV",
                data=selected.to_csv(index=False).encode("utf-8-sig"),
                file_name="cinema_filtered.csv", mime="text/csv",
                use_container_width=True, key="download_csv",
            )
    else:
        st.info("Выбери хотя бы один столбец для каталога.")

with tab_events:
    st.subheader("Новые премьеры и изменения дат")
    st.caption("Здесь тоже действуют общие фильтры. Вместо технической таблицы "
               "показаны карточки фильмов с датами и ссылками на Кинопоиск.")
    period = st.selectbox("Показать события за последние", [7, 30, 90, 365],
                          index=1, format_func=lambda n: f"{n} дней",
                          key="events_period")
    events = filtered_changes.copy()
    if not events.empty:
        events["when"] = pd.to_datetime(events["detected_at"],
                                       errors="coerce", utc=True)
        events = events[
            events["when"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=period)]
        events = events[events["field"].isin(["Новый фильм", "Дата премьеры"])]
        events = events[~(
            (events["field"] == "Новый фильм") &
            events["source"].fillna("").str.contains("Исходный Excel", case=False))]
        events = events.sort_values("when", ascending=False)
    movie_pool = f.copy()
    movie_pool["_fid"] = f_ids.values

    def event_cards(event_frame, label, is_shift=False):
        if event_frame.empty:
            st.info("За выбранный период таких событий в текущей выборке нет.")
            return
        last = event_frame.drop_duplicates("movie_id").head(500)
        event_ids = last["movie_id"].astype(str).tolist()
        display = movie_pool[movie_pool["_fid"].isin(event_ids)].copy()
        ordered = {fid: i for i, fid in enumerate(event_ids)}
        display["_order"] = display["_fid"].map(ordered)
        display = display.sort_values("_order")
        size = 12
        count = max(1, math.ceil(len(display) / size))
        key = "events_shift_page" if is_shift else "events_new_page"
        if st.session_state.get(key, 1) > count:
            st.session_state[key] = 1
        page = st.number_input("Страница карточек", min_value=1,
                               max_value=count, value=1, key=key)
        labels = {}
        notes = {}
        for _, event in last.iterrows():
            fid = str(event["movie_id"])
            labels[fid] = label
            if is_shift:
                notes[fid] = ("Изменение даты: " + str(event.get("old_value") or "неизвестно") +
                              " → " + str(event.get("new_value") or "неизвестно"))
        st.caption(f"Событий у {len(display)} фильмов · страница {page} из {count}.")
        card_grid(display.iloc[(page - 1) * size:page * size],
                  labels=labels, notes=notes)

    new_events = events[events["field"] == "Новый фильм"] if not events.empty else events
    date_events = events[
        (events["field"] == "Дата премьеры") &
        (events["old_value"].fillna("").str.strip() != "")
    ] if not events.empty else events

    st.markdown("### 🆕 Новые фильмы в базе")
    event_cards(new_events, "🆕 НОВАЯ ПРЕМЬЕРА")
    st.markdown("### 📅 Изменения дат премьер")
    event_cards(date_events, "📅 ИЗМЕНЕНИЕ ДАТЫ", is_shift=True)
    if not events.empty:
        st.download_button("Скачать журнал выбранных событий (CSV)",
                           events[["detected_at", "movie_id", "field", "old_value",
                                   "new_value", "source"]].to_csv(index=False).encode("utf-8-sig"),
                           "premiere_changes_filtered.csv", "text/csv",
                           key="events_csv")

with tab_logs:
    st.subheader("Журнал проверок Кинопоиска")
    if runs.empty:
        st.info("Проверок пока не было")
    else:
        st.dataframe(runs[["checked_at", "source", "status", "details", "found"]].head(300),
                     use_container_width=True, hide_index=True)
    st.caption("OK — карточки найдены; EMPTY — страница не содержала карточек; "
               "ERROR — доступ/разметка не позволили проверить источник. "
               "Ошибки не удаляют фильмы и не превращаются в отмену премьеры.")

st.caption("Источником служат российские календари и карточки Кинопоиска. "
           "Автоматизация работает только при доступности сайта для GitHub Actions; "
           "интерфейс показывает последнюю сохранённую проверку, а не данные в реальном времени.")