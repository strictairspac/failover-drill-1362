# 数据结构
本小型项目首次启动使用 SQLAlchemy metadata 建立 users/nodes/scenarios/drills/audits 五表，以及值班复盘域的 drill_overrides/drill_events/rollup_state 三表。自然键 users.email、nodes.name、scenarios.name 唯一。drills.scenario_id 为外键，快照在入队时固定。上线结构演进须补充版本化迁移，本项目不支持对既有不同结构数据库自动升级。

## 复盘域增量演进（幂等）
旧版库只有前五张表时，启动与 seed 会调用 `src/config/schema_init.py::init_schema`：
1. `Base.metadata.create_all` 自动补齐缺失的复盘三表；
2. 检查 information_schema / PRAGMA，若 `drills.override_verdict` 列不存在则 `ALTER TABLE` 补列（可空，旧数据保留）。
重复执行无副作用。复盘域表用途：
- `drill_overrides`：裁决更正流水，`(drill_id, version)` 唯一，当前裁决取最大 version。
- `drill_events`：逐演练事件账本，`(drill_id, version)` 唯一；补算事件固定 version=0，更正事件 version 从 1 递增，唯一约束保证重试不重复计数。
- `rollup_state`：单行（id=1）汇总状态，含补算游标 last_drill_id、事件游标 last_event_id、三类计数、counted_drills 与一次性切换标记 cutover。
