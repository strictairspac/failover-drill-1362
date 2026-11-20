"""值班复盘汇总域端到端验证（无外部依赖，SQLite + 进程内锁）。

覆盖题面要求：
1. 按剧本统计通过/阻断/失败场数
2. 旧演练分批补算，中断可恢复，重试上一批不重复计数
3. 补齐历史后一次性切换到可查状态
4. 切换后汇总落后也能查准查询发起时已提交场数，且不重扫旧演练
5. 更正与补算并发，每场只按当前裁决计一次
6. 页面所需接口：更正裁决、推进补算、查看场数与进度
运行：python3 tests/test_rollup.py
"""
import os
import sys
import tempfile
import threading

DB_FD, DB_PATH = tempfile.mkstemp(suffix='.db', prefix='rollup-test-')
os.close(DB_FD)
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['JWT_SECRET'] = 'test-secret'
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/0')  # 不可达，验证锁降级
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.config.schema_init import init_schema
from src.config.database import Session
from src.models.entities import User, Drill, DrillEvent, RollupState
from src.utils.security import hash_password
from src.services import rollup_service
from src.constants.enums import BACKFILL_BATCH_SIZE

PASS = 0
FAIL = 0

def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name} {detail}')

def make_drills(db, n_approved, n_blocked, n_failed):
    sid = 1
    for i in range(n_approved):
        db.add(Drill(scenario_id=sid, label=f'pass-{i}', status='completed',
                     snapshot={}, result={'decision': 'approved'}))
    for i in range(n_blocked):
        db.add(Drill(scenario_id=sid, label=f'block-{i}', status='completed',
                     snapshot={}, result={'decision': 'blocked'}))
    for i in range(n_failed):
        db.add(Drill(scenario_id=sid, label=f'fail-{i}', status='failed',
                     snapshot={}, result={'error': '必须且只能配置一个原主节点'}))
    db.flush()

def counts(s):
    return (s['counts']['approved'], s['counts']['blocked'], s['counts']['failed'], s['counts']['total'])

def reset():
    init_schema()
    with Session() as db:
        for row in db.scalars(__import__('sqlalchemy').select(Drill)).all():
            db.delete(row)
        db.query(DrillEvent).delete()
        db.query(RollupState).delete()
        for role in ('admin', 'operator', 'viewer'):
            email = role + '@hengshi.local'
            if not db.scalar(__import__('sqlalchemy').select(User).where(User.email == email)):
                db.add(User(email=email, role=role, password_hash=hash_password('Drill2026!')))
        db.commit()

print('== 1. 三分类裁决推导 ==')
reset()
with Session() as db:
    make_drills(db, 3, 2, 1)
    db.add(Drill(scenario_id=1, label='queued-x', status='queued', snapshot={}))  # 未产出=失败
    db.commit()
    s = rollup_service.summary(db)
    check('未切换前实时口径 3通过/2阻断/2失败（共7场）', counts(s) == (3, 2, 2, 7), str(s['counts']))
    check('初始未切换', s['cutover'] is False and s['mode'] == 'live')

print('== 2. 分批补算、中断恢复、重试幂等 ==')
with Session() as db:
    n = db.scalar(__import__('sqlalchemy').select(__import__('sqlalchemy').func.count(Drill.id)))
    expect_batches = -(-n // BACKFILL_BATCH_SIZE)
    results = []
    for _ in range(expect_batches + 2):  # 多调两次验证空转
        r = rollup_service.backfill_batch(db)
        results.append(r)
        db.commit()
    switched = [r for r in results if r['switched']]
    check('恰好在最后一个短批次切换一次', len(switched) == 1, str(results))
    check('切换后再调用为空转', results[-1]['scanned'] == 0 and results[-1]['switched'] is False)
    events = db.scalars(__import__('sqlalchemy').select(DrillEvent)).all()
    check('每场演练仅一条 version=0 补算事件（无重复计数）',
          len(events) == n and all(e.version == 0 and e.kind == 'backfill' for e in events))
    state = rollup_service.get_state(db)
    check('汇总计数与实时一致', (state.approved_count, state.blocked_count, state.failed_count) == (3, 2, 2))
    check('counted_drills 等于演练总数', state.counted_drills == n)
    s = rollup_service.summary(db)
    check('切换后 mode=rollup 且计数准确', s['mode'] == 'rollup' and counts(s) == (3, 2, 2, 7))

print('== 3. 中断恢复：补到一半重新开始，重试上一批不重复 ==')
reset()
with Session() as db:
    make_drills(db, 4, 4, 4)  # 12 场，批 5 -> 5/5/2
    db.commit()
with Session() as db:
    r1 = rollup_service.backfill_batch(db); db.commit()
    check('第一批 5 场', r1['scanned'] == 5 and r1['inserted'] == 5)
with Session() as db:  # 模拟 worker 重启：新会话从游标继续
    r2 = rollup_service.backfill_batch(db); db.commit()
    check('第二批从游标继续 5 场', r2['scanned'] == 5 and r2['inserted'] == 5)
with Session() as db:
    # 模拟中断后重试：直接再插一遍第一批的事件，全部应被唯一约束幂等拦截
    for d in db.scalars(__import__('sqlalchemy').select(Drill).where(Drill.id <= 5)).all():
        ok = rollup_service._insert_event(db, d.id, 0, rollup_service.current_verdict(db, d), 'backfill')
        check(f'演练{d.id} 重复补算事件被拦截', ok is False)
    db.commit()
    r3 = rollup_service.backfill_batch(db); db.commit()
    check('末批 2 场并切换', r3['scanned'] == 2 and r3['switched'] is True)
    s = rollup_service.summary(db)
    check('最终计数 4/4/4', counts(s) == (4, 4, 4, 12), str(s['counts']))
    ev_count = db.scalar(__import__('sqlalchemy').select(__import__('sqlalchemy').func.count(DrillEvent.id)))
    check('账本仍只有 12 条事件', ev_count == 12)

print('== 4. 切换后新演练只做增量实时合并，不重扫旧演练 ==')
with Session() as db:
    old_max = rollup_service.get_state(db).last_drill_id
    make_drills(db, 2, 1, 0)  # 新增 3 场
    db.commit()
    new_max = db.scalar(__import__('sqlalchemy').select(__import__('sqlalchemy').func.max(Drill.id)))
    s = rollup_service.summary(db)
    check('新提交场数立即查准 6/5/4', counts(s) == (6, 5, 4, 15), str(s['counts']))
    check('旧汇总未被重扫（游标不动）', s['rollup']['last_drill_id'] == old_max)
    check('进度显示游标后待并入 3 场', s['progress']['remaining_after_cursor'] == 3)
    # 再调 backfill：已切换，必须空转、绝不重扫
    r = rollup_service.backfill_batch(db); db.commit()
    check('切换后补算接口空转', r['scanned'] == 0)
    s2 = rollup_service.summary(db)
    check('空转后查询仍准确', counts(s2) == (6, 5, 4, 15))
    check('游标保持旧边界', s2['rollup']['last_drill_id'] == old_max and new_max > old_max)

print('== 5. 更正与补算并发：每场只按当前裁决计一次 ==')
reset()
with Session() as db:
    make_drills(db, 3, 2, 1)  # 6 场，批 5 -> 5 + 短批1，短批切换
    db.commit()
class Actor:
    email = 'operator@hengshi.local'
with Session() as db:
    # 5a. 切换前先更正一场（此时该场还没补算）
    target = db.scalar(__import__('sqlalchemy').select(Drill).where(Drill.label == 'pass-0'))
    v = rollup_service.correct_verdict(db, target, 'blocked', '复盘认定证据不足', Actor())
    check('更正版本 v1', v == 1 and target.override_verdict == 'blocked')
    db.commit()
with Session() as db:
    r1 = rollup_service.backfill_batch(db); db.commit()
    check('第一批满 5 场不切换', r1['scanned'] == 5 and r1['switched'] is False)
with Session() as db:
    r = rollup_service.backfill_batch(db); db.commit()
    check('短批 1 场时切换', r['scanned'] == 1 and r['switched'] is True)
    s = rollup_service.summary(db)
    # pass-0 由通过更正为阻断 => 2通过/3阻断/1失败
    check('先更正后补算：按当前裁决计一次', counts(s) == (2, 3, 1, 6), str(s['counts']))
with Session() as db:
    # 5b. 切换后再更正同一场两次，验证 delta 调整且始终只计一次
    target = db.scalar(
        __import__('sqlalchemy').select(Drill).where(Drill.label == 'pass-0'))
    rollup_service.correct_verdict(db, target, 'failed', '复核改判失败', Actor()); db.commit()
    s = rollup_service.summary(db)
    check('改判失败后 2/2/2', counts(s) == (2, 2, 2, 6), str(s['counts']))
with Session() as db:
    target = db.scalar(__import__('sqlalchemy').select(Drill).where(Drill.label == 'pass-0'))
    rollup_service.correct_verdict(db, target, 'approved', '最终恢复通过', Actor()); db.commit()
    s = rollup_service.summary(db)
    check('再改判通过后回到 3/2/1', counts(s) == (3, 2, 1, 6), str(s['counts']))
    state = rollup_service.get_state(db)
    check('counted_drills 始终=6（每场只计一次）', state.counted_drills == 6)
    evs = db.scalars(__import__('sqlalchemy').select(DrillEvent).where(
        DrillEvent.drill_id == target.id).order_by(DrillEvent.version)).all()
    check('该场事件链 version 0/1/2/3', [e.version for e in evs] == [0, 1, 2, 3])

print('== 6. 并发交错：补算进行中插入更正 ==')
reset()
with Session() as db:
    make_drills(db, 6, 0, 0)  # 6 场，批 5
    db.commit()
errs = []
def worker_batch():
    try:
        with Session() as db:
            rollup_service.backfill_batch(db); db.commit()
    except Exception as exc:
        errs.append(exc)
def worker_correct():
    try:
        with Session() as db:
            d = db.scalar(__import__('sqlalchemy').select(Drill).where(Drill.id == 6))
            rollup_service.correct_verdict(db, d, 'blocked', '并发更正', Actor()); db.commit()
    except Exception as exc:
        errs.append(exc)
t1 = threading.Thread(target=worker_batch)
t2 = threading.Thread(target=worker_correct)
t1.start(); t2.start(); t1.join(); t2.join()
check('并发工作线程无异常', not errs, str(errs))
with Session() as db:
    # 第二批补齐并切换
    r = rollup_service.backfill_batch(db); db.commit()
    s = rollup_service.summary(db)
    check('并发后最终口径：id=6 当前裁决为阻断 => 5通过/1阻断', counts(s) == (5, 1, 0, 6), str(s['counts']))
    state = rollup_service.get_state(db)
    check('覆盖场数 6 无重复', state.counted_drills == 6)

print('== 7. HTTP 接口与权限（FastAPI TestClient） ==')
from fastapi.testclient import TestClient
from src.main import app
with TestClient(app) as client:
    token = {}
    for role in ('admin', 'operator', 'viewer'):
        r = client.post('/auth/login', json={'email': f'{role}@hengshi.local', 'password': 'Drill2026!'})
        token[role] = r.json()['access_token']
    h = lambda r: {'Authorization': f'Bearer {token[r]}'}
    r = client.get('/rollup/summary', headers=h('viewer'))
    check('观察员可查汇总', r.status_code == 200 and 'counts' in r.json() and 'progress' in r.json())
    r = client.post('/rollup/backfill', headers=h('operator'))
    check('操作员不能推进补算 403', r.status_code == 403)
    r = client.post('/rollup/backfill', headers=h('admin'))
    check('管理员推进补算 200', r.status_code == 200 and 'summary' in r.json())
    r = client.post('/drills/99999/corrections', json={'verdict': 'blocked', 'reason': '复核改判'}, headers=h('admin'))
    check('更正不存在演练 404', r.status_code == 404)
    any_id = 1
    r = client.post(f'/drills/{any_id}/corrections', json={'verdict': 'nonsense', 'reason': '复核改判'}, headers=h('admin'))
    check('非法裁决 422', r.status_code == 422)
    r = client.post(f'/drills/{any_id}/corrections', json={'verdict': 'blocked', 'reason': '复核改判'}, headers=h('viewer'))
    check('观察员不能更正 403', r.status_code == 403)
    r = client.post(f'/drills/{any_id}/corrections', json={'verdict': 'blocked', 'reason': '复核改判'}, headers=h('operator'))
    check('操作员可更正 201', r.status_code == 201 and r.json()['verdict'] == 'blocked')
    r = client.get(f'/drills/{any_id}/corrections', headers=h('viewer'))
    check('更正流水可查', r.status_code == 200 and len(r.json()) >= 1)
    r = client.get('/drills', headers=h('viewer'))
    check('演练列表带 current_verdict', r.status_code == 200 and 'current_verdict' in r.json()[0])
    r = client.get('/rollup/summary', headers=h('viewer'))
    check('接口汇总计数仍准确（4通过/2阻断）',
          (r.json()['counts']['approved'], r.json()['counts']['blocked']) == (4, 2), str(r.json()['counts']))

os.unlink(DB_PATH)
print(f'\n结果：{PASS} 通过 / {FAIL} 失败')
sys.exit(1 if FAIL else 0)
