"""Apply the narrowly scoped bridge hooks to either portable or deployed version."""
import argparse
from pathlib import Path

def install(root):
    app=root/'app.py'; s=app.read_text(encoding='utf-8')
    if 'import browser_sync' not in s:
        anchor='import usage_icons'
        assert s.count(anchor)==1
        s=s.replace(anchor,anchor+'\nimport browser_sync')
    get='    def do_GET(self) -> None:\n        path = urlparse(self.path).path\n'
    if 'browser_sync.handle(self, path, json_response)' not in s:
        assert s.count(get)==1
        s=s.replace(get,get+'        if browser_sync.handle(self, path, json_response):\n            return\n')
    post='        if path == "/api/change-credentials":'
    if 'browser_sync.handle(self, path, json_response, payload)' not in s:
        assert s.count(post)==1
        s=s.replace(post,'        if browser_sync.handle(self, path, json_response, payload):\n            return\n'+post)
    route='        if path == "/cache.html":'
    if 'if path == "/browser-sync-ui.js":' not in s:
        assert s.count(route)==1
        s=s.replace(route,'        if path == "/browser-sync-ui.js":\n            file_response(self, ROOT / "browser-sync-ui.js", "application/javascript; charset=utf-8")\n            return\n'+route)
    compile(s,str(app),'exec'); app.write_text(s,encoding='utf-8',newline='\n')
    cache=root/'cache.html';s=cache.read_text(encoding='utf-8')
    if 'id="browserSyncBtn"' not in s:
        anchor='<nav class="sidebar-nav"'
        assert s.count(anchor)==1
        s=s.replace(anchor,'<button type="button" id="browserSyncBtn">↻ 自动同步登录</button>\n'+anchor)
    if 'src="/browser-sync-ui.js"' not in s:
        anchor='</script></body></html>'
        assert s.count(anchor)==1
        s=s.replace(anchor,'</script><script src="/browser-sync-ui.js"></script></body></html>')
    cache.write_text(s,encoding='utf-8',newline='\n')
    print('Installed browser sync hooks:',root)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);args=parser.parse_args();install(args.root)
