import streamlit as st,pandas as pd,plotly.express as px,json
from db import connect
st.set_page_config(page_title='Кинопремьеры России',page_icon='🎬',layout='wide')
st.title('🎬 Кинопремьеры России · 2026–2030')
st.caption('Мониторинг российских кинопремьер · источники, история изменений и состояние проверок')
c=connect(); rows=c.execute('SELECT * FROM movies').fetchall()
if not rows:st.warning('База пуста. Запустите python import_seed.py');st.stop()
df=pd.DataFrame([json.loads(r['payload']) for r in rows])
for col in ['Название','Дата премьеры','Страна','Жанры','Прокатчик в России','Рейтинг Кинопоиска','Количество оценок КП','ID фильма']:
 if col not in df:df[col]=None
# Seed dates are normalized ISO dates, but allow partial and future rows.
df['Дата']=pd.to_datetime(df['Дата премьеры'],errors='coerce');df['Год премьеры']=df['Дата'].dt.year
st.sidebar.header('Фильтры')
years=st.sidebar.multiselect('Год премьеры',[2026,2027,2028,2029,2030],default=[2026,2027,2028,2029,2030])
search=st.sidebar.text_input('Поиск по названию')
country=st.sidebar.text_input('Страна содержит')
genre=st.sidebar.text_input('Жанр содержит')
show_unknown=st.sidebar.checkbox('Включить фильмы без даты',value=True)
f=df[df['Год премьеры'].isin(years) | (df['Год премьеры'].isna() & show_unknown)].copy()
for col,q in [('Название',search),('Страна',country),('Жанры',genre)]:
 if q:f=f[f[col].fillna('').astype(str).str.contains(q,case=False,regex=False)]
changes=pd.read_sql_query('SELECT * FROM changes ORDER BY detected_at DESC',c)
runs=pd.read_sql_query('SELECT * FROM runs ORDER BY checked_at DESC',c)
new7=0;shift7=0
if not changes.empty:
 changes['when']=pd.to_datetime(changes['detected_at'],utc=True,errors='coerce')
 recent=changes[changes['when']>=pd.Timestamp.now(tz='UTC')-pd.Timedelta(days=7)]
 new7=int((recent['field']=='Новый фильм').sum());shift7=int((recent['field']=='Дата премьеры').sum())
a,b,d,e=st.columns(4)
a.metric('Фильмов в выборке',len(f));b.metric('Новых за 7 дней',new7);d.metric('Изменений дат за 7 дней',shift7);e.metric('Без даты',int(f['Дата'].isna().sum()))
left,right=st.columns([1,1])
with left:
 st.subheader('Премьеры по годам')
 counts=f.dropna(subset=['Год премьеры']).groupby('Год премьеры').size().reset_index(name='Фильмов')
 if not counts.empty:st.plotly_chart(px.bar(counts,x='Год премьеры',y='Фильмов',text='Фильмов'),use_container_width=True)
with right:
 st.subheader('По месяцам')
 monthly=f.dropna(subset=['Дата']).assign(Месяц=lambda x:x['Дата'].dt.to_period('M').astype(str)).groupby('Месяц').size().reset_index(name='Фильмов')
 if not monthly.empty:st.plotly_chart(px.line(monthly,x='Месяц',y='Фильмов',markers=True),use_container_width=True)
st.subheader('Каталог фильмов')
columns=['Дата премьеры','Название','Год производства','Страна','Жанры','Прокатчик в России','Рейтинг Кинопоиска','Количество оценок КП','ID фильма','Ссылка']
for x in columns:
 if x not in f:f[x]=None
st.dataframe(f[columns].sort_values('Дата премьеры',na_position='last'),use_container_width=True,hide_index=True,column_config={'Ссылка':st.column_config.LinkColumn('Карточка')})
st.download_button('Скачать отфильтрованную базу (CSV)',f[columns].to_csv(index=False).encode('utf-8-sig'),'cinema_filtered.csv','text/csv')
st.subheader('Журнал изменений')
if not changes.empty:st.dataframe(changes[['detected_at','movie_id','field','old_value','new_value','source']].head(500),use_container_width=True,hide_index=True)
else:st.info('Пока нет изменений')
st.subheader('Журнал проверок источников')
if not runs.empty:st.dataframe(runs[['checked_at','source','status','details','found']].head(50),use_container_width=True,hide_index=True)
st.caption('Важно: найденные ссылки не равны подтверждённым датам премьер. Изменения из календаря требуют доступного источника с датой.')
