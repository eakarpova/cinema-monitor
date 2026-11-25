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

r=S.get("https://www.kinobusiness.com/release/",timeout=20)
soup=BeautifulSoup(r.text,"html.parser")
for needle in ["2027","Фильтровать по:","Месяц:"]:
    at=r.text.find(needle)
    print("\nNEEDLE",needle,"POSITION",at,"NEAR",r.text[max(0,at-650):at+1150].replace("\n"," ")[:1800],flush=True)
for node in soup.select('[data-year],[data-value],[data-filter],a[href*="release"]')[:70]:
    print("FILTERNODE",str(node)[:500].replace("\n"," "),flush=True)
for node in soup.select("h2,h3,h4,.sortable__title,.sortable__date,.sortable__header")[:16]:
    print("DATEBLOCK",str(node)[:500].replace("\n"," "),flush=True)
for tag in soup.select("script[src]"):
    val=tag.get("src","")
    if "relea" in val or "filter" in val or "sortable" in val:
        print("JS",val,flush=True)
r=S.get("https://www.kinobusiness.com/movies/aytmatov/",timeout=20)
sub=BeautifulSoup(r.text,"html.parser")
print("\nDETAIL",r.status_code,len(r.content),r.url,flush=True)
for key in ["Описание","Сюжет","Синопсис","Премьера","Россия","2026"]:
    at=r.text.find(key)
    print("DETAILNEEDLE",key,at,r.text[max(at-220,0):at+700].replace("\n"," ")[:800],flush=True)
print("detail meta",[(t.get("name"),t.get("property"),t.get("content")) for t in sub.select("meta[name],meta[property]")[:20]],flush=True)

for url in ["https://www.kinobusiness.com/release/?2027",
            "https://www.kinobusiness.com/release/?2027&11",
            "https://www.kinobusiness.com/release/?YEAR=2027&MONTH=11",
            "https://www.kinobusiness.com/release/index.php?year=2027",
            "https://www.kinobusiness.com/release/?year=2027&month=9&distrebutor="]:
    try:
        r=S.get(url,timeout=16)
        page=BeautifulSoup(r.text,"html.parser")
        year=page.select_one("select#year option[selected]")
        month=page.select_one("select#month option[selected]")
        bricks=page.select(".sortable__brick")
        print("TRY",url,"status",r.status_code, "year",(year.get("value") if year else None),
              "month",(month.get("value") if month else None),"bricks",len(bricks),
              "bodylen",len(r.content),flush=True)
    except Exception as e:
        print("TRY ERROR",url,type(e).__name__,str(e),flush=True)
base=BeautifulSoup(S.get("https://www.kinobusiness.com/release/",timeout=16).text,"html.parser")
date=base.select_one(".sortable__date")
if date:
    print("DATEANCESTORS", [(p.name,p.get("class")) for p in list(date.parents)[:5]],flush=True)
    print("DATEPARENT",str(date.parent)[:3800].replace("\n"," "),flush=True)
for text in base.find_all("script"):
    script=text.string or ""
    if "year" in script and "month" in script and ("release" in script or "sortable" in script):
        print("SCRIPT",script[:1900].replace("\n"," "),flush=True)

r=S.get("https://www.kinobusiness.com/release/",timeout=20)
soup=BeautifulSoup(r.text,"html.parser")
alljs=[urljoin(r.url,tag["src"]) for tag in soup.select("script[src]")]
print("JSFILES",len(alljs),[(x.split("/")[-1],x[:140]) for x in alljs[-25:]],flush=True)
for url in alljs[-12:]:
    try:
        js=S.get(url,timeout=12).text
        findings=[]
        for q in ["sortable__select","distrebutor","/release/","year","month","ajax"]:
            at=js.find(q)
            if at>=0:
                findings.append((q,js[max(0,at-170):at+300].replace("\n"," ")[:450]))
        if findings:
            print("JSFIND",url,len(js),findings[:6],flush=True)
    except Exception as e:
        print("JSERR",str(e)[:90],flush=True)
