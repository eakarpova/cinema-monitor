"""Cinema Monitor — interactive Streamlit dashboard, backed by cinema.sqlite."""
import json

import pandas as pd
import plotly.express as px
import streamlit as st
from db import connect

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
fields = ["Название", "Дата премьеры", "Год календаря", "Год производства",
          "Страна", "Жанры", "Прокатчик в России", "Рейтинг Кинопоиска",
          "Количество оценок КП", "ID фильма", "Ссылка", "Источник даты"]
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

selected_years = df["Год фильтра"].isin(years)
no_year = df["Год фильтра"].isna() & show_unknown
f = df[selected_years | no_year].copy()
if not show_unknown:
    f = f[f["Дата"].notna()]
for col, query in [("Название", search), ("Страна", country), ("Жанры", genre)]:
    if query:
        f = f[f[col].fillna("").astype(str).str.contains(query, case=False, regex=False)]

recent = pd.DataFrame()
if not changes.empty:
    changes["when"] = pd.to_datetime(changes["detected_at"], utc=True, errors="coerce")
    recent = changes[changes["when"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)]
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
metric_b.metric("Новых за 7 дней", new7)
metric_c.metric("Изменений дат за 7 дней", shift7)
metric_d.metric("Без точной даты", int(f["Дата"].isna().sum()))

st.caption("Часть дат исходной выгрузки представлена первым числом месяца. "
           "Для точного планирования проката проверяйте поле «Источник даты» и карточку фильма.")

tab_overview, tab_catalog, tab_events, tab_logs = st.tabs(
    ["📊 Аналитика", "🎞 Каталог", "🔔 Новинки и переносы", "⚙️ Проверки источников"])

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
    st.subheader("По прокатчикам")
    distributors = f["Прокатчик в России"].dropna().astype(str)
    distributors = distributors[distributors.str.strip() != ""]
    if not distributors.empty:
        top = distributors.value_counts().head(15).rename_axis("Прокатчик").reset_index(name="Фильмов")
        st.plotly_chart(px.bar(top, x="Фильмов", y="Прокатчик", orientation="h"),
                        use_container_width=True)

with tab_catalog:
    st.subheader("Каталог фильмов")
    columns = ["Дата премьеры", "Название", "Год производства", "Страна", "Жанры",
               "Прокатчик в России", "Рейтинг Кинопоиска", "Количество оценок КП",
               "ID фильма", "Источник даты", "Ссылка"]
    selected = f.sort_values("Дата", na_position="last")[columns]
    st.dataframe(selected, use_container_width=True, hide_index=True,
                 column_config={"Ссылка": st.column_config.LinkColumn("Карточка")})
    st.download_button("Скачать базу по текущим фильтрам (CSV)",
                       selected.to_csv(index=False).encode("utf-8-sig"),
                       "cinema_filtered.csv", "text/csv")

with tab_events:
    st.subheader("Новые премьеры и изменения")
    if changes.empty:
        st.info("Пока нет зарегистрированных изменений")
    else:
        events = changes[changes["field"].isin(["Новый фильм", "Дата премьеры"])]
        events = events[~((events["field"] == "Новый фильм") &
                          events["source"].fillna("").str.contains("Исходный Excel", case=False))]
        if events.empty:
            st.info("После первоначальной загрузки новых премьер и переносов пока не зафиксировано")
        else:
            st.dataframe(events[["detected_at", "movie_id", "field", "old_value",
                                 "new_value", "source"]].head(1000),
                         use_container_width=True, hide_index=True)
    with st.expander("Все изменения полей"):
        if not changes.empty:
            st.dataframe(changes[["detected_at", "movie_id", "field", "old_value",
                                 "new_value", "source"]].head(1000),
                         use_container_width=True, hide_index=True)

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