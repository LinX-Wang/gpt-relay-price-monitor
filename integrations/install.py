"""Fetch pinned upstream tools and apply the exported LinX hosting adapters."""
from pathlib import Path
import argparse,tarfile,urllib.request,tempfile,shutil,json
ROOT=Path(__file__).resolve().parents[1]
SOURCES={
 'ikale':('Ikaleio/lm-detector','175355e8939c2ee7aaeeac2b22186fa5940412c3'),
 'meow':('chen-006/meow-llm-detector','c56dc0691e53d3a567a7f675ed9a23d878294636'),
}
def install(name):
    repo,commit=SOURCES[name];target=ROOT/'vendor'/name
    if target.exists():raise SystemExit(f'{target} already exists; preserve its data before upgrading.')
    with tempfile.TemporaryDirectory() as tmp:
        archive=Path(tmp)/'source.tar.gz'
        req=urllib.request.Request(f'https://codeload.github.com/{repo}/tar.gz/{commit}',headers={'User-Agent':'LinX-detector-installer'})
        with urllib.request.urlopen(req,timeout=90) as response,archive.open('wb') as output:shutil.copyfileobj(response,output)
        unpack=Path(tmp)/'source';unpack.mkdir()
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if member.issym() or member.islnk() or member.isdev() or not (unpack/member.name).resolve().is_relative_to(unpack.resolve()):
                    raise RuntimeError('Unsafe archive member')
            tar.extractall(unpack,filter='data')
        src=next(unpack.iterdir());target.parent.mkdir(exist_ok=True);shutil.copytree(src,target)
    overlay=ROOT/'integrations'/name/'overlay'
    if overlay.exists():shutil.copytree(overlay,target,dirs_exist_ok=True)
    (target/'LINX_UPSTREAM.json').write_text(json.dumps({'repository':repo,'commit':commit},indent=2))
    print(f'Installed {name} at {target}. Follow docs/DETECTORS.md to install its dependencies and run it.')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('tool',choices=SOURCES);args=parser.parse_args();install(args.tool)
