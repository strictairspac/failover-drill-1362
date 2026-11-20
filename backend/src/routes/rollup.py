from fastapi import APIRouter, Depends
from src.config.database import get_db
from src.middlewares.auth import current_user, writable, admin
from src.schemas.rollup import CorrectionInput
from src.services import rollup_service
from src.services.drill_service import get_drill
from src.controllers.actions import audit
from src.utils.locking import named_lock, LockBusy
from fastapi import HTTPException

router = APIRouter()

@router.get('/rollup/summary')
def rollup_summary(db=Depends(get_db), user=Depends(current_user)):
    """复盘场数与补算进度。切换前实时全量；切换后汇总+增量实时合并。"""
    return rollup_service.summary(db)

@router.post('/drills/{drill_id}/corrections', status_code=201)
def correct(drill_id: int, data: CorrectionInput, db=Depends(get_db), user=Depends(writable)):
    """更正一场演练的当前裁决；可与补算并发，每场只按当前裁决计一次。"""
    try:
        verdict, reason = data.normalized()
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    drill = get_drill(db, drill_id)
    version = rollup_service.correct_verdict(db, drill, verdict, reason, user)
    audit(db, user, f'drill:correct:{drill.id}:{verdict}:v{version}')
    db.commit()
    return {'drill_id': drill.id, 'version': version, 'verdict': verdict,
            'overrides': rollup_service.list_overrides(db, drill.id)}

@router.get('/drills/{drill_id}/corrections')
def corrections(drill_id: int, db=Depends(get_db), user=Depends(current_user)):
    get_drill(db, drill_id)
    return rollup_service.list_overrides(db, drill_id)

@router.post('/rollup/backfill')
def run_backfill(db=Depends(get_db), user=Depends(admin)):
    """推进下一批历史补算。互斥锁串行化；重试上一批不重复计数。"""
    try:
        with named_lock(rollup_service.ROLLUP_LOCK):
            result = rollup_service.backfill_batch(db)
            audit(db, user, 'rollup:backfill')
            db.commit()
    except LockBusy as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    result['summary'] = rollup_service.summary(db)
    return result
