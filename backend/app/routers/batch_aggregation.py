"""成组批量聚合同由：源事件摄入 → 聚合组提交 → 整组计算/原组补做 → 仪表盘取数。

所有状态流转都委托 cube_builder / dashboard_builder 领域服务，路由层不做业务判断。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import batch_aggregation as ba
from app.database import db
from app.services import service_for

router = APIRouter(prefix="/api/batch-aggregation", tags=["成组批量聚合"])


class IngestBatchPayload(BaseModel):
    batch_id: str
    region_counts: dict[str, int] = Field(default_factory=dict)
    channel_counts: dict[str, int] = Field(default_factory=dict)


class CreateGroupPayload(BaseModel):
    group_code: str
    group_name: str
    batch_ids: list[str]


class RunGroupPayload(BaseModel):
    # 模拟部分计算失败的数据集（如 ["channel"]），失败项不写结果
    fail_dataset_keys: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 源事件批次
# ---------------------------------------------------------------------------

@router.post("/events/batch")
def ingest_source_batch(payload: IngestBatchPayload) -> dict[str, Any]:
    """摄入一批带明确批次 ID 的源事件。"""
    svc = service_for("cube_builder")
    return {"ok": True, "result": svc.ingest_source_batch(
        payload.batch_id, payload.region_counts, payload.channel_counts)}


@router.get("/events/batches")
def list_event_batches() -> dict[str, Any]:
    """列出当前所有源事件批次及计数。"""
    rows = db.fetchall(
        "SELECT batch_id, COUNT(*) as event_count FROM events "
        "WHERE batch_id IS NOT NULL GROUP BY batch_id ORDER BY MIN(id)"
    )
    return {"ok": True, "items": rows}


# ---------------------------------------------------------------------------
# 聚合组
# ---------------------------------------------------------------------------

@router.get("/groups")
def list_groups() -> dict[str, Any]:
    svc = service_for("cube_builder")
    return {"ok": True, "items": svc.list_batch_groups()}


@router.get("/groups/{group_id}")
def get_group(group_id: int) -> dict[str, Any]:
    svc = service_for("cube_builder")
    group = svc.get_batch_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"聚合组 {group_id} 不存在")
    return {"ok": True, "item": group}


@router.post("/groups")
def create_group(payload: CreateGroupPayload) -> dict[str, Any]:
    """新增一次提交：固定同一批源事件，初始化地区/渠道两条数据集记录。"""
    svc = service_for("cube_builder")
    try:
        group = svc.create_batch_group(payload.group_code, payload.group_name, payload.batch_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": group}


@router.post("/groups/{group_id}/run")
def run_group(group_id: int, payload: RunGroupPayload) -> dict[str, Any]:
    """整组批量聚合，逐条记录完成数据；支持模拟部分失败。"""
    svc = service_for("cube_builder")
    try:
        result = svc.run_batch_group(group_id, fail_dataset_keys=payload.fail_dataset_keys)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/groups/{group_id}/backfill")
def backfill_group(group_id: int) -> dict[str, Any]:
    """原组补做：只跑缺失项，已完成数据集原样保留。"""
    svc = service_for("cube_builder")
    try:
        result = svc.backfill_batch_group(group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# 仪表盘取数（只读取完成记录，携带版本与批次核对信息）
# ---------------------------------------------------------------------------

@router.get("/dashboard/region")
def region_chart(group_id: int | None = None) -> dict[str, Any]:
    """地区图：可指定聚合组，默认读取最新组的地区数据集完成记录。"""
    return service_for("dashboard_builder").region_chart(group_id)


@router.get("/dashboard/region-channel")
def region_channel_chart(group_id: int | None = None) -> dict[str, Any]:
    """地区渠道联图：同组两条完成记录齐备才出数，拒绝跨提交拼装。"""
    return service_for("dashboard_builder").region_channel_chart(group_id)


@router.get("/dashboard")
def batch_dashboard(group_id: int | None = None) -> dict[str, Any]:
    """成组聚合仪表盘：地区图 + 联图 + 聚合组完整状态。"""
    return service_for("dashboard_builder").batch_dashboard(group_id)


# ---------------------------------------------------------------------------
# 场景装载：一键复现「原组 70/80、新组 90 且渠道失败、后到事件 61」
# ---------------------------------------------------------------------------

@router.post("/scenario/load")
def load_scenario() -> dict[str, Any]:
    """重建题述验收场景（幂等：先清掉本功能的聚合组与场景事件批次）。"""
    # 仅清理成组聚合相关数据，不影响其他模块
    db.execute("DELETE FROM aggregation_dataset_cells")
    db.execute("DELETE FROM aggregation_group_datasets")
    db.execute("DELETE FROM aggregation_groups")
    for batch in ("BATCH_OLD_70_80", "BATCH_NEW_90_60", "BATCH_LATE_61"):
        db.execute("DELETE FROM events WHERE batch_id=?", [batch])

    cube_svc = service_for("cube_builder")

    # 原组源事件：地区合计 70，渠道合计 80
    cube_svc.ingest_source_batch(
        "BATCH_OLD_70_80",
        {"华东": 40, "华南": 30},
        {"app": 45, "web": 35},
    )
    # 新组源事件：地区合计 90，渠道本应合计 60
    cube_svc.ingest_source_batch(
        "BATCH_NEW_90_60",
        {"华东": 50, "华北": 40},
        {"app": 32, "mini_program": 28},
    )
    # 后到事件：补做前摄入，只带渠道维度，使新批次渠道合计为 61
    cube_svc.ingest_source_batch(
        "BATCH_LATE_61",
        {},
        {"app": 1},
    )

    old_group = cube_svc.create_batch_group(
        "GRP_OLD", "原组（地区70/渠道80）", ["BATCH_OLD_70_80"])
    cube_svc.run_batch_group(old_group["id"])

    new_group = cube_svc.create_batch_group(
        "GRP_NEW", "新组（地区90/渠道60，渠道计算失败）", ["BATCH_NEW_90_60"])
    # 部分计算失败：地区先算出 90，渠道本应为 60 却没写出来
    cube_svc.run_batch_group(new_group["id"], fail_dataset_keys=[ba.DATASET_CHANNEL])

    return {
        "ok": True,
        "message": "场景已装载：原组 70/80 整组完成；新组地区 90 完成、渠道失败；后到批次 BATCH_LATE_61 已摄入",
        "old_group_id": old_group["id"],
        "new_group_id": new_group["id"],
    }
