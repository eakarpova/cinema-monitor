import csv,sys
from db import connect,upsert,log
from pathlib import Path
p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent/'data'/'initial.csv'
c=connect(); count=0
with p.open(encoding='utf-8-sig',newline='') as f:
 for row in csv.DictReader(f):
  fid=str(row.get('ID фильма','')).strip()
  if not fid:continue
  row['id']=fid
  upsert(c,row,'Исходный Excel');count+=1
log(c,'Исходный Excel','OK',f'Импортировано {count} строк',count)
print('Импортировано:',count)
