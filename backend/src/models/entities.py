from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, JSON, DateTime, ForeignKey, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base

def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(250))
class Node(Base):
    __tablename__ = 'nodes'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    term: Mapped[int] = mapped_column(Integer)
    log_position: Mapped[int] = mapped_column(Integer)
    available: Mapped[bool] = mapped_column(Boolean)
    fenced: Mapped[bool] = mapped_column(Boolean)
    vote_enabled: Mapped[bool] = mapped_column(Boolean)
class Scenario(Base):
    __tablename__ = 'scenarios'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    fail_primary: Mapped[bool] = mapped_column(Boolean)
    fence_primary: Mapped[bool] = mapped_column(Boolean)
    max_data_loss: Mapped[int] = mapped_column(Integer)
    required_term: Mapped[int] = mapped_column(Integer)
class Drill(Base):
    __tablename__ = 'drills'
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey('scenarios.id'))
    label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default='queued')
    snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 人工更正后的当前裁决；为空时按计算结果/状态推导
    override_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class DrillOverride(Base):
    """裁决更正流水：同一场演练的当前裁决取 version 最大的一条。"""
    __tablename__ = 'drill_overrides'
    id: Mapped[int] = mapped_column(primary_key=True)
    drill_id: Mapped[int] = mapped_column(ForeignKey('drills.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint('drill_id', 'version', name='uq_override_drill_version'),)
class DrillEvent(Base):
    """逐演练事件账本：补算/更正都只追加事件，(drill_id, version) 唯一保证幂等。"""
    __tablename__ = 'drill_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    drill_id: Mapped[int] = mapped_column(ForeignKey('drills.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))  # backfill | correction
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint('drill_id', 'version', name='uq_event_drill_version'),)
class RollupState(Base):
    """复盘汇总单行状态：id 固定为 1。"""
    __tablename__ = 'rollup_state'
    id: Mapped[int] = mapped_column(primary_key=True)
    last_drill_id: Mapped[int] = mapped_column(Integer, default=0)      # 补算游标：已扫描到的最大演练 id
    last_event_id: Mapped[int] = mapped_column(Integer, default=0)      # 已入账事件游标
    cutover: Mapped[bool] = mapped_column(Boolean, default=False)       # 历史补齐后一次性切换
    approved_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    counted_drills: Mapped[int] = mapped_column(Integer, default=0)     # 汇总覆盖的演练场数
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
class Audit(Base):
    __tablename__ = 'audits'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
