"""Check tracked release files for accidental state or credential disclosure."""
from pathlib import Path
import re,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
problems=[]
for name in filter(None,files):
    path=Path(name)
    if path.parts[0] in {'data','storage','reports','backups','vendor','node_modules'} or name in {'sites.json','quality_sites.json','.env','latest.csv','sites.public.json'} or path.suffix in {'.enc','.key','.pem','.sqlite3','.db'}:
        problems.append(name+': private/generated path')
    target=ROOT/path
    if not target.is_file() or path.suffix.lower() in {'.png','.jpg','.jpeg','.woff','.woff2'}:continue
    text=target.read_text(encoding='utf-8-sig',errors='replace')
    if re.search(r'eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}',text):problems.append(name+': possible JWT')
    if re.search(r'\bsk-[A-Za-z0-9_-]{32,}\b',text):problems.append(name+': possible model credential')
if problems:
    print('\n'.join(problems));raise SystemExit(1)
print(f'PASS: {len([x for x in files if x])} tracked files; no forbidden runtime files or apparent API credentials.')
