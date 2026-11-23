"""Conservative calendar discovery: never invent a release date or overwrite with blank values."""
import requests,re,time,random,datetime,sys
from bs4 import BeautifulSoup
from db import connect,upsert,log
BASE='https://www.kinopoisk.ru/premiere/ru/{year}/'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; CinemaPremiereResearch/1.0; +https://github.com/)','Accept-Language':'ru-RU,ru;q=0.9'}
def discover(html):
 soup=BeautifulSoup(html,'html.parser'); out={}
 # Calendar HTML may change; only accept explicit film links, never infer premiere dates from production year.
 for a in soup.select('a[href*="/film/"]'):
  href=a.get('href','');m=re.search(r'/film/(\d+)',href)
  if not m:continue
  title=a.get_text(' ',strip=True)
  if len(title)<2:continue
  fid=m.group(1)
  if fid not in out:out[fid]={'id':fid,'ID фильма':fid,'Название':title,'Ссылка':'https://www.kinopoisk.ru/film/'+fid+'/','Год календаря':a.get('data-year')}
 return list(out.values())
def run():
 c=connect(); total=0
 for year in range(2026,2031):
  url=BASE.format(year=year)
  try:
   r=requests.get(url,headers=HEADERS,timeout=25)
   if r.status_code!=200:raise RuntimeError(f'HTTP {r.status_code}')
   if any(s in r.text.lower() for s in ['captcha','challenge-solver','доступ ограничен']):raise RuntimeError('Возможная защита от автоматических запросов')
   movies=discover(r.text)
   if not movies:raise RuntimeError('Не найдены ссылки на фильмы; вероятно, изменился HTML или страница не отдана')
   for m in movies:upsert(c,m,url)
   log(c,url,'OK','Обнаружены ссылки; даты не обновлялись без подтверждения',len(movies));total+=len(movies)
   print(year,len(movies))
  except Exception as e:
   log(c,url,'ERROR',str(e));print(year,'ERROR',e,file=sys.stderr)
  time.sleep(random.uniform(2,4))
 print('Обнаружено ссылок:',total)
if __name__=='__main__':run()
