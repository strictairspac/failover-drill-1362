import json
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from src.config.database import get_db
from src.middlewares.auth import current_user, writable
from src.schemas.drill import DrillInput
from src.services.drill_service import create_drill, list_drills, get_drill, drill_view
from src.controllers.actions import audit
router = APIRouter()
@router.get('/drills')
def listing(db=Depends(get_db), user=Depends(current_user)): return list_drills(db)
@router.post('/drills', status_code=202)
def create(data: DrillInput, db=Depends(get_db), user=Depends(writable)):
    obj = create_drill(db, data); audit(db, user, f'drill:enqueue:{obj.id}'); db.commit()
    # DB is the durable queue; worker claims queued rows transactionally.
    return drill_view(db, obj)
@router.get('/drills/{drill_id}')
def detail(drill_id: int, db=Depends(get_db), user=Depends(current_user)):
    return drill_view(db, get_drill(db, drill_id))
@router.get('/drills/{drill_id}/export')
def export(drill_id: int, db=Depends(get_db), user=Depends(current_user)):
    obj = get_drill(db, drill_id)
    payload = drill_view(db, obj)
    return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="drill-{obj.id}.json"'})
