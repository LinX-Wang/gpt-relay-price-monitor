"""Private backup of project files and state. Keep the resulting ZIP out of Git."""
import os,sys,zipfile,json
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from settings import ROOT,DATA_DIR,SITES_FILE

def main():
    folder=ROOT/'backups';folder.mkdir(exist_ok=True)
    target=folder/('linx-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.zip')
    skip={'.git','.venv','venv','node_modules','vendor','backups','dist','__pycache__','.pytest_cache'}
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for current,dirs,files in os.walk(ROOT):
            dirs[:]=[d for d in dirs if d not in skip]
            for name in files:
                path=Path(current)/name
                if not path.is_symlink():archive.write(path,path.relative_to(ROOT).as_posix())
        if not DATA_DIR.is_relative_to(ROOT):
            for path in DATA_DIR.rglob('*'):
                if path.is_file() and not path.is_symlink():archive.write(path,'external-storage/'+path.relative_to(DATA_DIR).as_posix())
        archive.writestr('BACKUP_MANIFEST.json',json.dumps({'created_at':datetime.now().isoformat(),'external_storage':not DATA_DIR.is_relative_to(ROOT),'contains_private_credentials':True},indent=2))
    print(f'Private backup saved: {target}')
if __name__=='__main__':main()
