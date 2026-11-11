"""Stable purity group identity; historical multipliers remain immutable."""
import sqlite3,uuid,time,math,re
from pathlib import Path
from settings import DB_FILE as DB
class Conflict(ValueError):pass
def rate_key(value):
 try:n=float(value)
 except (ValueError,TypeError):raise ValueError('请填写有效的新倍率')
 if not math.isfinite(n) or n<0:raise ValueError('倍率应为大于等于 0 的有限数值')
 return format(n,'.12g')
def scope_rate(scope):
 m=re.fullmatch(r'倍率\s+([0-9.eE+-]+)x',str(scope))
 if not m:return None
 try:return rate_key(m.group(1))
 except ValueError:return None
def model_key(model):return str(model or 'GPT-6.1-Sol').strip().casefold()
def ensure_group(c,site,model,rate):
 key=rate_key(rate);mk=model_key(model)
 old=c.execute('SELECT group_id FROM purity_group_rates WHERE site_name=? AND model_key=? AND rate_key=?',(site,mk,key)).fetchone()
 if old:return old[0]
 gid=uuid.uuid4().hex;now=time.strftime('%Y-%m-%d %H:%M:%S')
 c.execute('INSERT INTO purity_groups(id,site_name,model,model_key,current_multiplier,version,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)',(gid,site,str(model or 'GPT-6.1-Sol').strip(),mk,float(key),now,now))
 c.execute('INSERT INTO purity_group_rates(site_name,model_key,rate_key,multiplier,group_id) VALUES(?,?,?,?,?)',(site,mk,key,float(key),gid))
 return gid
def initialize(db=None):
 with sqlite3.connect(db or DB,timeout=20) as c:
  c.execute('CREATE TABLE IF NOT EXISTS purity_groups(id TEXT PRIMARY KEY,site_name TEXT NOT NULL,model TEXT NOT NULL,model_key TEXT NOT NULL,current_multiplier REAL NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,merged_into TEXT)')
  c.execute('CREATE TABLE IF NOT EXISTS purity_group_rates(site_name TEXT NOT NULL,model_key TEXT NOT NULL,rate_key TEXT NOT NULL,multiplier REAL NOT NULL,group_id TEXT NOT NULL,PRIMARY KEY(site_name,model_key,rate_key))')
  c.execute('CREATE TABLE IF NOT EXISTS purity_group_events(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id TEXT NOT NULL,site_name TEXT NOT NULL,model TEXT NOT NULL,action TEXT NOT NULL,from_multiplier REAL,to_multiplier REAL,merged_group_id TEXT,created_at TEXT NOT NULL)')
  tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if 'purity_checks' in tables:
   for site,model,rate in c.execute('SELECT DISTINCT site_name,model,multiplier FROM purity_checks WHERE multiplier IS NOT NULL').fetchall():
    try:ensure_group(c,site,model,rate)
    except ValueError:pass
  if 'purity_decisions' in tables:
   for site,model,scope in c.execute('SELECT DISTINCT site_name,model,scope FROM purity_decisions').fetchall():
    rate=scope_rate(scope)
    if rate is not None:ensure_group(c,site,model,rate)
def mapping(db=None):
 initialize(db)
 with sqlite3.connect(db or DB,timeout=20) as c:
  c.row_factory=sqlite3.Row
  return {(r['site_name'],r['model_key'],r['rate_key']):dict(r) for r in c.execute('SELECT a.site_name,a.model_key,a.rate_key,a.group_id,g.current_multiplier,g.version FROM purity_group_rates a JOIN purity_groups g ON a.group_id=g.id WHERE g.merged_into IS NULL')}
def annotate(history,db=None):
 aliases=mapping(db);out=[]
 for row in history:
  r=dict(row)
  try:g=aliases.get((r['site_name'],model_key(r.get('model')),rate_key(r.get('multiplier'))))
  except ValueError:g=None
  if g:r.update(group_id=g['group_id'],current_multiplier=g['current_multiplier'])
  out.append(r)
 return out
def data(db=None):
 initialize(db)
 with sqlite3.connect(db or DB,timeout=20) as c:
  c.row_factory=sqlite3.Row
  groups=[dict(r) for r in c.execute('SELECT * FROM purity_groups WHERE merged_into IS NULL ORDER BY site_name,model_key,current_multiplier')]
  aliases=[dict(r) for r in c.execute('SELECT * FROM purity_group_rates')]
  events=[dict(r) for r in c.execute('SELECT * FROM purity_group_events ORDER BY id DESC')]
  checks=[dict(r) for r in c.execute('SELECT * FROM purity_checks ORDER BY checked_at DESC,id DESC')] if c.execute("SELECT 1 FROM sqlite_master WHERE name='purity_checks'").fetchone() else []
 by_id={g['id']:g for g in groups};lookup={}
 for g in groups:g['aliases']=[];g['latest_check']=None
 for a in aliases:
  if a['group_id'] in by_id:
   by_id[a['group_id']]['aliases'].append(a['multiplier']);lookup[(a['site_name'],a['model_key'],a['rate_key'])]=by_id[a['group_id']]
 for check in checks:
  try:g=lookup.get((check['site_name'],model_key(check.get('model')),rate_key(check['multiplier'])))
  except ValueError:g=None
  if g is not None and g['latest_check'] is None:g['latest_check']=check
 return {'groups':groups,'events':events}
def change(payload,db=None):
 initialize(db)
 action=payload.get('action')
 if action not in ('reprice','merge'):raise ValueError('请选择调价或合并操作')
 with sqlite3.connect(db or DB,timeout=20) as c:
  c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
  source=c.execute('SELECT * FROM purity_groups WHERE id=? AND merged_into IS NULL',(payload.get('group_id'),)).fetchone()
  if not source or source['site_name']!=payload.get('site_name'):raise ValueError('所选分组不存在，请刷新后重试')
  try:version=int(payload.get('version',-1))
  except (ValueError,TypeError):raise ValueError('分组版本无效，请刷新后重试')
  if source['version']!=version:raise Conflict('分组已被其他页面修改，请刷新后重新选择')
  target=None
  if action=='merge':
   target=c.execute('SELECT * FROM purity_groups WHERE id=? AND merged_into IS NULL',(payload.get('target_group_id'),)).fetchone()
   if not target or target['id']==source['id'] or target['site_name']!=source['site_name'] or target['model_key']!=source['model_key']:raise ValueError('只能合并同一站点、同一模型的不同分组')
   if str(target['version'])!=str(payload.get('target_version')):raise Conflict('目标分组已变化，请刷新后重新选择')
   key=rate_key(target['current_multiplier'])
  else:key=rate_key(payload.get('new_multiplier'))
  alias=c.execute('SELECT group_id FROM purity_group_rates WHERE site_name=? AND model_key=? AND rate_key=?',(source['site_name'],source['model_key'],key)).fetchone()
  if alias and alias['group_id']!=source['id'] and (target is None or alias['group_id']!=target['id']):raise ValueError('该倍率已有独立验纯记录，请选择“合并为同一分组”')
  if target is None and float(key)==source['current_multiplier']:raise ValueError('新倍率与当前倍率相同')
  now=time.strftime('%Y-%m-%d %H:%M:%S')
  if target:
   c.execute('UPDATE purity_group_rates SET group_id=? WHERE group_id=?',(source['id'],target['id']))
   c.execute('UPDATE purity_groups SET merged_into=?,version=version+1,updated_at=? WHERE id=?',(source['id'],now,target['id']))
  if not alias:c.execute('INSERT INTO purity_group_rates(site_name,model_key,rate_key,multiplier,group_id) VALUES(?,?,?,?,?)',(source['site_name'],source['model_key'],key,float(key),source['id']))
  c.execute('UPDATE purity_groups SET current_multiplier=?,version=version+1,updated_at=? WHERE id=?',(float(key),now,source['id']))
  c.execute('INSERT INTO purity_group_events(group_id,site_name,model,action,from_multiplier,to_multiplier,merged_group_id,created_at) VALUES(?,?,?,?,?,?,?,?)',(source['id'],source['site_name'],source['model'],action,source['current_multiplier'],float(key),target['id'] if target else None,now))
 return {'ok':True,'group_id':source['id'],'current_multiplier':float(key)}
