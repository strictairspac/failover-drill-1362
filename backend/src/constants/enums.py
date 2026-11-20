ROLES = ('admin', 'operator', 'viewer')
DRILL_STATUSES = ('queued', 'running', 'completed', 'failed')
# 值班复盘裁决口径：允许切主=通过；安全阻断=阻断；计算失败/尚未产出结果=失败
VERDICTS = ('approved', 'blocked', 'failed')
VERDICT_LABELS = {'approved': '通过', 'blocked': '阻断', 'failed': '失败'}
# 补算每批扫描的演练数量
BACKFILL_BATCH_SIZE = 5
