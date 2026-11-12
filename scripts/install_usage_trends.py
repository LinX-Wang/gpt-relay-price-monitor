"""Install additive trend routes without replacing production app files."""
from pathlib import Path
import argparse
def install(root):
    p=root/'app.py';s=p.read_text(encoding='utf-8');nl=chr(10)
    if 'import usage_trends' not in s:s=s.replace('import usage_icons','import usage_icons'+nl+'import usage_trends',1)
    anchor='                elif path == "/api/cache/snapshot":'
    if 'path == "/api/cache/trend"' not in s:
        assert s.count(anchor)==1;s=s.replace(anchor,'                elif path == "/api/cache/trend": result = usage_trends.trend(query)'+nl+anchor)
    anchor='        if path == "/cache.html":'
    if 'path == "/usage-trends.js"' not in s:
        assert s.count(anchor)==1;s=s.replace(anchor,nl.join(['        if path == "/usage-trends.js":','            file_response(self, ROOT / "usage-trends.js", "application/javascript; charset=utf-8")','            return',anchor]))
    compile(s,str(p),'exec');p.write_text(s,encoding='utf-8',newline=nl)
    p=root/'cache.html';s=p.read_text(encoding='utf-8')
    if 'src="/usage-trends.js"' not in s:s=s.replace('</body>','<script src="/usage-trends.js"></script></body>')
    p.write_text(s,encoding='utf-8',newline=nl);print('Installed usage trend routes')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);install(p.parse_args().root)
