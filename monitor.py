"""Ежедневная проверка ссылок календаря. Не выдумывает даты российских премьер."""
import re, sys, time, random
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from db import connect, upsert, log

BASE = 'https://www.kinopoisk.ru/premiere/ru/{year}/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36',
           'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.7'}
BAD_TITLES = {'подробнее', 'смотреть', 'трейлер', 'о фильме', 'кинопоиск', 'купить билет', 'билеты'}

def discover(html):
    soup = BeautifulSoup(html, 'html.parser')
    out = {}
    for a in soup.select('a[href*="/film/"]'):
        href = urljoin('https://www.kinopoisk.ru', a.get('href', ''))
        if urlparse(href).netloc not in ('www.kinopoisk.ru', 'kinopoisk.ru'):
            continue
        m = re.search(r'/film/(\d+)(?:/|$|\?)', href)
        if not m:
            continue
        fid = m.group(1)
        title = ' '.join(a.get_text(' ', strip=True).split())
        # Не перезаписываем названия сомнительным текстом ссылок.
        if len(title) < 3 or title.lower() in BAD_TITLES or len(title) > 180:
            continue
        if fid not in out:
            out[fid] = {'id': fid, 'ID фильма': fid, 'Название': title,
                        'Ссылка': f'https://www.kinopoisk.ru/film/{fid}/'}
    return list(out.values())

def run():
    c = connect()
    total = 0
    errors = 0
    for year in range(2026, 2031):
        url = BASE.format(year=year)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            page = r.text.lower()
            if any(x in page for x in ('captcha', 'challenge-solver', 'доступ ограничен')):
                raise RuntimeError('Защита сайта / CAPTCHA')
            movies = discover(r.text)
            if not movies:
                raise RuntimeError('Ссылки на фильмы не найдены: возможно, блокировка или изменение разметки')
            # Только проверенные ID и названия. Дату из ссылки определить нельзя.
            for movie in movies:
                upsert(c, movie, url)
            log(c, url, 'OK', 'Найдены ID/названия; даты премьер не проверены', len(movies))
            total += len(movies)
            print(f'{year}: {len(movies)} ссылок')
        except Exception as exc:
            errors += 1
            log(c, url, 'ERROR', str(exc))
            print(f'{year}: ERROR {exc}', file=sys.stderr)
        time.sleep(random.uniform(1, 2))
    c.close()
    print(f'Всего ссылок: {total}; ошибок источников: {errors}')
    # Все источники недоступны — явно сигнализируем об ошибке workflow.
    if errors == 5:
        sys.exit(1)

if __name__ == '__main__':
    run()
