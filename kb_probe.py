"""Read-only DOM/form probe for the secondary premiere source. Never store page HTML."""
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 (compatible; CinemaMonitorResearch/1.0)","Accept-Language":"ru-RU,ru;q=0.9"})
urls=["https://www.kinobusiness.com/release/","https://www.kinobusiness.com/release/?year=2027",
      "https://www.kinobusiness.com/release/?year=2026&month=11"]
for url in urls:
    print("\n===URL",url,flush=True)
    try:
        r=S.get(url, timeout=20)
        print("status",r.status_code,"final",r.url,"length",len(r.content), flush=True)
        soup=BeautifulSoup(r.text,"html.parser")
        for form in soup.select("form")[:8]:
            print("FORM",str(form.get("action")),form.get("method"), 
                [(e.name,e.get("name"),e.get("value"),e.get("id")) for e in form.select("input,select")[:20]],flush=True)
            for sel in form.select("select")[:6]:
                print("SELECT",sel.get("name"), [(o.get("value"),o.get_text(" ",strip=True)) for o in sel.select("option")[:10]],flush=True)
        movie_links=[a for a in soup.select("a[href]") if "/movies/" in a["href"]]
        print("MOVIELINKS",len(movie_links),flush=True)
        for a in movie_links[:3]:
            print("MOVIE",urljoin(url,a["href"]),a.get_text(" ",strip=True),
                "parents",[(p.name,p.get("class"),p.get("id")) for p in list(a.parents)[:6]],
                flush=True)
            for p in list(a.parents)[:6]:
                if p.name in ["div","article","li"] and len(p.get_text(" ",strip=True))>50:
                    print("BLOCK",str(p)[:2900].replace("\n"," "),flush=True)
                    break
        pages=[a.get("href") for a in soup.select("a[href]") if "page=" in a.get("href","").lower()]
        print("PAGINATION",pages[:15],flush=True)
        print("TITLES",[(a.get("href"),a.get_text(" ",strip=True)[:55]) for a in soup.select("a[href]") if "2027" in a.get_text(" ",strip=True)][:12],flush=True)
    except Exception as e:
        print("ERROR",type(e).__name__,str(e),flush=True)
