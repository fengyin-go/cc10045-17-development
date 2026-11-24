"""业务路由汇总：资源路由 + 管道路由 + 成组批量聚合路由。"""
from __future__ import annotations

from app.routers import batch_aggregation
from app.routers import resource
from app.routers import pipeline

# 专用路由（/api/batch-aggregation/...）必须在通用资源路由（/api/{module}/...）之前注册，
# 否则 GET /api/batch-aggregation/groups 会被通用路由的 {entry_id} 抢先匹配。
ROUTERS = [batch_aggregation, resource]
PIPELINE_ROUTER = pipeline.router
