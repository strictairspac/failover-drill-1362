"""值班复盘汇总域。

口径：
- 裁决三分类：approved=通过，blocked=阻断，failed=失败（计算失败或尚未产出结果）。
- 每场演练的当前裁决 = 最新一条人工更正；没有更正则按计算结果/状态推导。
- 补算按 id 游标分批扫描旧演练，事件账本 (drill_id, version) 唯一，
  重试上一批不会重复计数，中断后从游标继续。
- 更正与补算可并发：更正追加更高 version 的事件，汇总只认每场的最新裁决，
  每场演练在任一时刻只按当前裁决计一次。
- 历史补齐前查询实时全量扫描已提交演练；补齐后一次性置 cutover，
  之后汇总只覆盖 id <= 游标的旧演练，新演练 (id > 游标) 实时合并，永不重扫旧演练。
"""
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Drill, DrillOverride, DrillEvent, RollupState
from src.constants.enums import VERDICTS, BACKFILL_BATCH_SIZE

ROLLUP_LOCK = 'rollup-backfill'

def derived_verdict(drill):
    """无人工更正时的裁决推导口径。"""
    if drill.override_verdict:
        return drill.override_verdict
    if drill.result and drill.result.get('decision') in ('approved', 'blocked'):
        return drill.result['decision']
    return 'failed'

def current_verdict(db, drill):
    """当前裁决：优先取最新更正，否则推导。"""
    if drill.override_verdict:
        return drill.override_verdict
    return derived_verdict(drill)

def get_state(db):
    state = db.get(RollupState, 1)
    if state is None:
        state = RollupState(id=1)
        db.add(state); db.flush()
    return state

def _insert_event(db, drill_id, version, verdict, kind):
    """追加事件；唯一冲突说明该版本已入账，返回 False 表示幂等跳过。"""
    try:
        with db.begin_nested():
            db.add(DrillEvent(drill_id=drill_id, version=version, verdict=verdict, kind=kind))
        return True
    except IntegrityError:
        return False

def _next_override_version(db, drill_id):
    latest = db.scalar(select(func.coalesce(func.max(DrillOverride.version), 0))
                       .where(DrillOverride.drill_id == drill_id))
    return latest + 1

def _applied_verdict(db, state, drill_id):
    """该演练在已入账事件中的旧裁决（用于增量 delta），无则 None。"""
    return db.scalar(
        select(DrillEvent.verdict)
        .where(DrillEvent.id <= state.last_event_id, DrillEvent.drill_id == drill_id)
        .order_by(DrillEvent.version.desc(), DrillEvent.id.desc()).limit(1))

def _apply_events(db, state):
    """消费新事件，增量维护汇总计数。

    游标之后的新演练尚未纳入补算范围，事件只标记消费、不进汇总，
    它们由查询路径实时合并（避免双重计数）。
    """
    new_events = db.scalars(
        select(DrillEvent).where(DrillEvent.id > state.last_event_id)
        .order_by(DrillEvent.id)).all()
    consumed = 0
    for ev in new_events:
        if ev.drill_id > state.last_drill_id:
            # 事件所属演练尚未补算到：停在这里等待游标推进，
            # 不能只推进事件游标——否则该场裁决会被标记消费却从未计数。
            break
        old = _applied_verdict(db, state, ev.drill_id)
        if old is None:
            setattr(state, ev.verdict + '_count', getattr(state, ev.verdict + '_count') + 1)
            state.counted_drills += 1
        elif old != ev.verdict:
            setattr(state, old + '_count', getattr(state, old + '_count') - 1)
            setattr(state, ev.verdict + '_count', getattr(state, ev.verdict + '_count') + 1)
        # old == ev.verdict：裁决未变，计数不动
        state.last_event_id = ev.id
        consumed += 1
    return consumed

def correct_verdict(db, drill, verdict, reason, user):
    """人工更正裁决：写更正流水 + 冗余当前裁决 + 追加幂等事件。"""
    if verdict not in VERDICTS:
        raise HTTPException(422, f'裁决必须是 {"/".join(VERDICTS)} 之一')
    version = _next_override_version(db, drill.id)
    db.add(DrillOverride(drill_id=drill.id, version=version, verdict=verdict,
                         reason=reason, actor=user.email))
    drill.override_verdict = verdict
    # 更正事件 version 从 1 起；补算事件固定 version=0，天然不冲突
    inserted = _insert_event(db, drill.id, version, verdict, 'correction')
    state = get_state(db)
    if inserted:
        _apply_events(db, state)
    db.flush()
    return version

def backfill_batch(db, limit=BACKFILL_BATCH_SIZE):
    """推进一批历史补算。返回本批处理摘要；已切换则直接返回当前状态。"""
    state = get_state(db)
    if state.cutover:
        return _status(state, scanned=0, inserted=0, consumed=0, switched=False)
    batch = db.scalars(
        select(Drill).where(Drill.id > state.last_drill_id)
        .order_by(Drill.id).limit(limit)).all()
    inserted = 0
    for drill in batch:
        verdict = current_verdict(db, drill)
        if _insert_event(db, drill.id, 0, verdict, 'backfill'):
            inserted += 1
        state.last_drill_id = drill.id
    consumed = _apply_events(db, state)
    switched = False
    # 扫到队尾（短批次或空批次）且账本覆盖全部演练时，一次性切换到可查状态。
    # 空批次覆盖总数恰为批大小整数倍的情形。
    if len(batch) < limit:
        total = db.scalar(select(func.count(Drill.id)))
        covered = db.scalar(select(func.count(func.distinct(DrillEvent.drill_id))))
        if total == covered:
            state.cutover = True
            switched = True
    db.flush()
    return _status(state, scanned=len(batch), inserted=inserted,
                   consumed=consumed, switched=switched)

def _live_counts(db, drill_ids=None):
    """对指定（或全部）演练按当前裁决实时计数。"""
    stmt = select(Drill)
    if drill_ids is not None:
        stmt = stmt.where(Drill.id.in_(drill_ids))
    counts = {v: 0 for v in VERDICTS}
    for drill in db.scalars(stmt).all():
        counts[current_verdict(db, drill)] += 1
    return counts

def summary(db):
    """复盘汇总查询。切换前全量实时；切换后汇总 + 游标后新演练实时合并。"""
    state = get_state(db)
    total_drills = db.scalar(select(func.count(Drill.id)))
    pending = db.scalar(select(func.count(Drill.id)).where(Drill.id > state.last_drill_id))
    if not state.cutover:
        counts = _live_counts(db)
        mode = 'live'
    else:
        counts = {v: getattr(state, v + '_count') for v in VERDICTS}
        fresh = db.scalars(select(Drill.id).where(Drill.id > state.last_drill_id)).all()
        if fresh:
            extra = _live_counts(db, fresh)
            for v in VERDICTS:
                counts[v] += extra[v]
        mode = 'rollup'
    covered = db.scalar(select(func.count(func.distinct(DrillEvent.drill_id)))) or 0
    return {
        'mode': mode,
        'cutover': bool(state.cutover),
        'counts': {'approved': counts['approved'], 'blocked': counts['blocked'],
                   'failed': counts['failed'], 'total': sum(counts.values())},
        'rollup': {'approved': state.approved_count, 'blocked': state.blocked_count,
                   'failed': state.failed_count, 'counted_drills': state.counted_drills,
                   'last_drill_id': state.last_drill_id, 'last_event_id': state.last_event_id},
        'progress': {'covered_drills': covered, 'total_drills': total_drills,
                     'remaining_after_cursor': pending,
                     'batch_size': BACKFILL_BATCH_SIZE, 'last_drill_id': state.last_drill_id},
        'updated_at': state.updated_at.isoformat() if state.updated_at else None,
    }

def _status(state, scanned, inserted, consumed, switched):
    return {'scanned': scanned, 'inserted': inserted, 'consumed': consumed,
            'switched': switched, 'cutover': bool(state.cutover),
            'last_drill_id': state.last_drill_id}

def list_overrides(db, drill_id):
    rows = db.scalars(
        select(DrillOverride).where(DrillOverride.drill_id == drill_id)
        .order_by(DrillOverride.version.desc())).all()
    return [{'version': r.version, 'verdict': r.verdict, 'reason': r.reason,
             'actor': r.actor, 'created_at': r.created_at.isoformat()} for r in rows]
