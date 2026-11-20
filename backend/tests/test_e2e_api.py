"""旧库演进与 HTTP 全流程验证。

A. 模拟旧版五表库（无 override_verdict、无复盘三表），执行 init_schema 后：
   - 旧数据完整保留；新列可空；新表建立且可正常补算。
B. 通过 HTTP API 走完整业务：入队 -> 模拟 worker 计算 -> 更正 ->
   管理员分批推进补算至切换 -> 切换后再提交新演练，场数即时查准且不重扫。
"""
import os, sys, tempfile

fd, DB_PATH = tempfile.mkstemp(suffix='.db'); os.close(fd)
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['JWT_SECRET'] = 'e2e-secret'
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/0')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PASS = FAIL = 0
def check(name, cond, detail=''):
    global PASS, FAIL
    if cond: PASS += 1; print(f'  ✓ {name}')
    else: FAIL += 1; print(f'  ✗ {name} {detail}')

from sqlalchemy import create_engine, text, inspect
from src.config.settings import DATABASE_URL

print('== A. 旧五表库结构演进 ==')
legacy = create_engine(DATABASE_URL)
with legacy.begin() as conn:
    conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(120), role VARCHAR(20), password_hash VARCHAR(250))"))
    conn.execute(text("CREATE TABLE nodes (id INTEGER PRIMARY KEY, name VARCHAR(80), role VARCHAR(20), term INTEGER, log_position INTEGER, available BOOLEAN, fenced BOOLEAN, vote_enabled BOOLEAN)"))
    conn.execute(text("CREATE TABLE scenarios (id INTEGER PRIMARY KEY, name VARCHAR(100), fail_primary BOOLEAN, fence_primary BOOLEAN, max_data_loss INTEGER, required_term INTEGER)"))
    conn.execute(text("CREATE TABLE drills (id INTEGER PRIMARY KEY, scenario_id INTEGER, label VARCHAR(120), status VARCHAR(20), snapshot JSON, result JSON, created_at DATETIME)"))
    conn.execute(text("CREATE TABLE audits (id INTEGER PRIMARY KEY, actor VARCHAR(120), action VARCHAR(120), created_at DATETIME)"))
    conn.execute(text("INSERT INTO scenarios (id,name,fail_primary,fence_primary,max_data_loss,required_term) VALUES (1,'旧剧本',1,1,0,7)"))
    conn.execute(text("INSERT INTO drills (id,scenario_id,label,status,snapshot,result) VALUES (1,1,'旧演练','completed','{}','{\"decision\":\"approved\"}')"))
    conn.execute(text("INSERT INTO users (id,email,role,password_hash) VALUES (1,'admin@hengshi.local','admin','x')"))

from src.config.schema_init import init_schema
init_schema()
insp = inspect(legacy)
tables = set(insp.get_table_names())
check('复盘三表已建立', {'drill_overrides','drill_events','rollup_state'} <= tables, str(tables))
check('drills 新列已补齐', any(c['name']=='override_verdict' for c in insp.get_columns('drills')))
with legacy.connect() as conn:
    row = conn.execute(text('SELECT label, status, override_verdict FROM drills WHERE id=1')).fetchone()
    check('旧演练数据保留且新列为 NULL', row == ('旧演练','completed',None), str(row))
    check('旧剧本保留', conn.execute(text("SELECT name FROM scenarios WHERE id=1")).scalar() == '旧剧本')
# 幂等：再跑一次不报错
init_schema()
check('init_schema 重复执行幂等', True)
legacy.dispose()

print('== B. HTTP 全流程 ==')
from fastapi.testclient import TestClient
from src.main import app
from src.config.database import Session
from src.models.entities import User, Node, Scenario, Drill
from src.utils.security import hash_password
from src.services.election import elect
from sqlalchemy import select

with Session() as db:
    # 清理 A 段遗留的旧库假用户（密码哈希不可用）与旧演练，再按正式口径建数据
    for u in db.scalars(select(User)).all():
        db.delete(u)
    for d in db.scalars(select(Drill)).all():
        db.delete(d)
    db.add(Node(name='hs-primary', role='leader', term=6, log_position=8100, available=True, fenced=False, vote_enabled=True))
    db.add(Node(name='hs-east', role='follower', term=6, log_position=8100, available=True, fenced=False, vote_enabled=True))
    db.add(Node(name='hs-west', role='follower', term=6, log_position=8098, available=True, fenced=False, vote_enabled=True))
    db.add(Node(name='hs-east', role='follower', term=6, log_position=8100, available=True, fenced=False, vote_enabled=True))
    for role in ('admin','operator','viewer'):
        e = role+'@hengshi.local'
        if not db.scalar(select(User).where(User.email==e)):
            db.add(User(email=e, role=role, password_hash=hash_password('Drill2026!')))
    db.add(Scenario(name='晚班隔离演练', fail_primary=True, fence_primary=True, max_data_loss=0, required_term=7))
    db.add(Scenario(name='缺少隔离证明', fail_primary=True, fence_primary=False, max_data_loss=0, required_term=7))
    db.commit()

def run_worker_once():
    """复刻 worker：claim 一个 queued 演练并计算。"""
    with Session() as db:
        job = db.scalar(select(Drill).where(Drill.status=='queued').order_by(Drill.id))
        if not job: return None
        job.status = 'running'
        try:
            job.result = elect(job.snapshot['nodes'], job.snapshot['scenario'])
            job.status = 'completed'
        except ValueError as exc:
            job.result = {'error': str(exc), 'simulation': True}
            job.status = 'failed'
        db.commit()
        return job.id, job.status

with TestClient(app) as c:
    tok = {}
    for role in ('admin','operator','viewer'):
        tok[role] = c.post('/auth/login', json={'email':f'{role}@hengshi.local','password':'Drill2026!'}).json()['access_token']
    H = lambda r: {'Authorization': f'Bearer {tok[r]}'}

    # 8 场演练：6 通过（隔离剧本）+ 2 阻断（无 fencing 剧本）
    for i in range(6):
        r = c.post('/drills', json={'scenario_id':2,'label':f'晚班通过-{i}'}, headers=H('operator'))
        check(f'入队 {i}', r.status_code == 202)
    for i in range(2):
        r = c.post('/drills', json={'scenario_id':3,'label':f'阻断案例-{i}'}, headers=H('operator'))
        check(f'阻断入队 {i}', r.status_code == 202)
    for _ in range(8): run_worker_once()

    s = c.get('/rollup/summary', headers=H('viewer')).json()
    check('切换前实时口径 6通过/2阻断/0失败',
          (s['counts']['approved'],s['counts']['blocked'],s['counts']['failed'],s['counts']['total'])==(6,2,0,8), str(s['counts']))

    # 补算前操作员先更正一场为失败（列表按 id 倒序，末位即最早入队的演练）
    first_id = c.get('/drills', headers=H('viewer')).json()[-1]['id']
    r = c.post(f'/drills/{first_id}/corrections', json={'verdict':'failed','reason':'复盘发现快照节点缺失'}, headers=H('operator'))
    check('补算前更正成功', r.status_code==201 and r.json()['version']==1)

    # 管理员分批推进（批大小 5：5 + 短批3 -> 第二次切换）
    r1 = c.post('/rollup/backfill', headers=H('admin')).json()
    check('第一批 5 场未切换', r1['scanned']==5 and r1['switched'] is False, str(r1))
    r2 = c.post('/rollup/backfill', headers=H('admin')).json()
    check('第二批 3 场并切换', r2['scanned']==3 and r2['switched'] is True, str(r2))
    s = c.get('/rollup/summary', headers=H('viewer')).json()
    check('切换后口径 5通过/2阻断/1失败',
          (s['counts']['approved'],s['counts']['blocked'],s['counts']['failed'])==(5,2,1), str(s['counts']))
    check('模式已切换 rollup', s['mode']=='rollup' and s['cutover'] is True)

    # 切换后再提交 4 场（3 通过 1 阻断），不调用补算，查询必须立即查准
    for i in range(3):
        c.post('/drills', json={'scenario_id':2,'label':f'切换后通过-{i}'}, headers=H('operator'))
    c.post('/drills', json={'scenario_id':3,'label':'切换后阻断'}, headers=H('operator'))
    for _ in range(4): run_worker_once()
    s = c.get('/rollup/summary', headers=H('viewer')).json()
    check('汇总落后下查询仍查准 8/3/1（共12场）',
          (s['counts']['approved'],s['counts']['blocked'],s['counts']['failed'],s['counts']['total'])==(8,3,1,12), str(s['counts']))
    check('游标后 4 场实时合并', s['progress']['remaining_after_cursor']==4)
    r3 = c.post('/rollup/backfill', headers=H('admin')).json()
    check('切换后补算空转不重扫', r3['scanned']==0 and r3['cutover'] is True)
    s2 = c.get('/rollup/summary', headers=H('viewer')).json()
    check('空转后口径不变', s2['counts']==s['counts'])

    # 更正历史旧演练：delta 调整，场数仍准确
    r = c.post(f'/drills/{first_id}/corrections', json={'verdict':'approved','reason':'复核恢复原裁决'}, headers=H('admin'))
    check('二次更正版本 v2', r.status_code==201 and r.json()['version']==2)
    s = c.get('/rollup/summary', headers=H('viewer')).json()
    check('更正后 9/3/0', (s['counts']['approved'],s['counts']['blocked'],s['counts']['failed'])==(9,3,0), str(s['counts']))

    # 审计留痕
    audits = c.get('/audits', headers=H('admin')).json()
    actions = {a['action'] for a in audits}
    check('审计包含补算与更正动作', any(x.startswith('rollup:backfill') for x in actions) and any(x.startswith('drill:correct') for x in actions), str(sorted(actions)[:6]))

    # 详情接口带更正流水与当前裁决
    d = c.get(f'/drills/{first_id}', headers=H('viewer')).json()
    check('详情含 current_verdict 与两条更正', d['current_verdict']=='approved' and len(d['overrides'])==2)

os.unlink(DB_PATH)
print(f'\n结果：{PASS} 通过 / {FAIL} 失败')
sys.exit(1 if FAIL else 0)
