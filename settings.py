"""Portable paths and local environment; no production data is bundled."""
from pathlib import Path
import os
ROOT=Path(__file__).resolve().parent
def load_environment():
    path=ROOT/'.env'
    if not path.exists():return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        key,value=line.split('=',1);key=key.strip();value=value.strip()
        if key.startswith(('APP_','METAPI_','MEOW_','IKALE_')):
            if len(value)>1 and value[0]==value[-1] and value[0] in (chr(34),chr(39)):value=value[1:-1]
            os.environ.setdefault(key,value)
load_environment()
DATA_DIR=Path(os.environ.get('APP_DATA_DIR',str(ROOT/'data'))).expanduser().resolve()
REPORTS_DIR=DATA_DIR/'reports' if os.environ.get('APP_DATA_DIR') else ROOT/'reports'
SITES_FILE=DATA_DIR/'sites.json' if os.environ.get('APP_DATA_DIR') else ROOT/'sites.json'
DB_FILE=DATA_DIR/'price_monitor.sqlite3'
def initialize_storage():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    REPORTS_DIR.mkdir(parents=True,exist_ok=True)
    if not SITES_FILE.exists():SITES_FILE.write_text('[]\n',encoding='utf-8')
initialize_storage()
