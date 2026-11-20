"""结构初始化与对既有库的幂等演进。

- 全新库：SQLAlchemy metadata 一次性建立全部表。
- 既有库（旧版只有五表）：补齐复盘域三张表与 drills.override_verdict 列，
  列存在与否按方言查询 information_schema / PRAGMA，不破坏既有数据。
"""
import logging
from sqlalchemy import inspect, text
from src.config.database import engine
from src.models import entities

logger = logging.getLogger('schema')

def _column_exists(inspector, table, column):
    return any(col['name'] == column for col in inspector.get_columns(table))

def init_schema():
    inspector = inspect(engine)
    entities.Base.metadata.create_all(engine)
    if 'drills' in inspector.get_table_names() and not _column_exists(inspector, 'drills', 'override_verdict'):
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE drills ADD COLUMN override_verdict VARCHAR(20) NULL'))
        logger.info('drills.override_verdict 列已补齐')
