"""User conclusions, independent of measured matching percentages."""
import sqlite3,time,html
import purity_groups as groups
from pathlib import Path
from urllib.parse import urlsplit
from settings import DB_FILE as DB
STATUSES=('推荐','避用','待复核','取消标记')
def multiplier_scope(value):
 try:return f'倍率 {float(value):g}x' if value is not None else '倍率未记录'
 except (ValueError,TypeError):return '倍率未记录'
def auto_status(match):
 try:
  value=float(match)
 except (ValueError,TypeError):return '待复核'
 if value==0:return '避用'
 return '推荐' if 95<value<=100 else '待复核'
def display_status(status):
 return {'待复核':'一般','避用':'避雷'}.get(status,status)
def initialize(db=None):
 with sqlite3.connect(db or DB,timeout=20) as c:
  c.execute('CREATE TABLE IF NOT EXISTS purity_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT,site_name TEXT NOT NULL,model TEXT NOT NULL,scope TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL DEFAULT "",report_url TEXT NOT NULL DEFAULT "",checked_at TEXT NOT NULL,created_at TEXT NOT NULL)')
def validate(p):
 def text(k,default='',limit=2000):
  v=str(p.get(k,default) or '').strip()
  if len(v)>limit:raise ValueError('内容过长：'+k)
  return v
 v={k:text(k) for k in ('site_name','model','reason','report_url')}
 v['scope']=text('scope','未注明分组',200) or '未注明分组';v['status']={'一般':'待复核','中性':'待复核','避雷':'避用'}.get(text('status'),text('status'));v['checked_at']=text('checked_at',limit=50).replace('T',' ') or time.strftime('%Y-%m-%d %H:%M')
 if not v['site_name'] or not v['model']:raise ValueError('请选择站点并填写模型；整站结论请填写“整站”')
 if v['status'] not in STATUSES:raise ValueError('请选择有效的使用结论')
 if v['report_url']:
  u=urlsplit(v['report_url'])
  if u.scheme not in ('https','http') or not u.netloc:raise ValueError('报告链接应为 http 或 https 地址')
 return v
def insert_validated(c,v):
 c.execute('INSERT INTO purity_decisions(site_name,model,scope,status,reason,report_url,checked_at,created_at) VALUES(?,?,?,?,?,?,?,?)',tuple(v[k] for k in ('site_name','model','scope','status','reason','report_url','checked_at'))+(time.strftime('%Y-%m-%d %H:%M:%S'),))
def save(p,db=None):
 v=validate(p);initialize(db)
 with sqlite3.connect(db or DB,timeout=20) as c:
  insert_validated(c,v)
def data(db=None):
 initialize(db)
 with sqlite3.connect(db or DB,timeout=20) as c:
  c.row_factory=sqlite3.Row;history=[dict(r) for r in c.execute('SELECT * FROM purity_decisions ORDER BY id DESC')]
 aliases=groups.mapping(db)
 current={};seen=set()
 for r in history:
  rate=groups.scope_rate(r['scope']);g=aliases.get((r['site_name'],groups.model_key(r['model']),rate)) if rate is not None else None
  key=(r['site_name'],r['model'].lower().strip(),g['group_id'] if g else r['scope'])
  if key in seen:continue
  seen.add(key)
  if r['status']!='取消标记':
   visible=dict(r)
   if g:visible.update(scope=multiplier_scope(g['current_multiplier']),group_id=g['group_id'])
   current.setdefault(r['site_name'],[]).append(visible)
 for values in current.values():values.sort(key=lambda r:({'避用':0,'待复核':1,'推荐':2}[r['status']],-r['id']))
 return {'current':current,'history':history}
def badges(records):
 out=[]
 for r in records:
  short={'gpt-6.1-sol':'6.1-sol','gpt-6-sol':'6-sol','gpt-6-astra':'6-astra'}.get(r['model'].lower(),r['model'])
  cls={'推荐':'recommend','避用':'avoid','待复核':'review'}[r['status']]
  label=short+' '+display_status(r['status'])+' · '+r['scope'];title=' · '.join([r['model'],r['scope'],display_status(r['status']),r['checked_at'],r['reason']])
  out.append('<span class="decision-badge decision-'+cls+'" title="'+html.escape(title,quote=True)+'">'+html.escape(label)+'</span>')
 return ''.join(out)

def backfill_latest(sites,db=None):
 """Fill missing model/rate conclusions from the latest recorded score; never replace a decision."""
 initialize(db)
 existing=data(db)['history']
 occupied={(r['site_name'],r['model'].strip().lower(),r['scope']) for r in existing}
 unspecified={(r['site_name'],r['model'].strip().lower()) for r in existing if not r['scope'].startswith('倍率 ')}
 names={s['name'] for s in sites};seen=set();candidates=[]
 with sqlite3.connect(db or DB,timeout=20) as c:
  c.row_factory=sqlite3.Row
  rows=[dict(r) for r in c.execute('SELECT * FROM purity_checks ORDER BY checked_at DESC,id DESC')]
 for r in rows:
  if r['site_name'] not in names or r['match_percent'] is None:continue
  model=r.get('model','').strip() or 'gpt-6.1-sol'
  scope=multiplier_scope(r['multiplier']);key=(r['site_name'],model.lower(),scope)
  if key in seen:continue
  seen.add(key)
  if key in occupied or key[:2] in unspecified:continue
  score=float(r['match_percent'])
  if not 0<=score<=100:continue
  candidates.append(validate({'site_name':r['site_name'],'model':model,'scope':scope,'status':auto_status(score),'checked_at':r['checked_at'],'reason':f'历史验纯记录自动补标：匹配度 {score:g}%。'+(' 原备注：'+r['notes'] if r['notes'] else '')}))
 with sqlite3.connect(db or DB,timeout=20) as c:
  for r in candidates:insert_validated(c,r)
 return candidates
