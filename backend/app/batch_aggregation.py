"""成组批量聚合管道：一次提交固定同一批源事件，逐条产出地区/渠道数据集。

核心不变量：
1. 聚合组（aggregation_groups）在创建时把源事件批次 ID 快照到 event_batch_ids，
   之后整组的所有计算（含补做）只读取这批事件，后摄入的事件不会串入旧组。
2. 每个数据集（aggregation_group_datasets）逐条记录状态、值、版本与所用批次；
   状态为「已完成」的记录不可变，补做只跑「待计算/计算失败」的缺失项。
3. 仪表盘只读取「已完成」记录；联图要求两条记录同属一个聚合组，
   因此不可能拼出两次提交的结果。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

from app.database import db

# 组内固定的两个数据集：地区数据集与渠道数据集
DATASET_REGION = "region"
DATASET_CHANNEL = "channel"
DATASET_SPECS: list[dict[str, str]] = [
    {"dataset_key": DATASET_REGION, "dimension_name": "region", "label": "地区数据集"},
    {"dataset_key": DATASET_CHANNEL, "dimension_name": "channel", "label": "渠道数据集"},
]
DATASET_KEYS = [spec["dataset_key"] for spec in DATASET_SPECS]

STATUS_PENDING = "待计算"
STATUS_RUNNING = "计算中"
STATUS_COMPLETED = "已完成"
STATUS_FAILED = "计算失败"

GROUP_STATUS_PENDING = "待计算"
GROUP_STATUS_RUNNING = "计算中"
GROUP_STATUS_PARTIAL = "部分完成"
GROUP_STATUS_COMPLETED = "整组完成"


# ============================================================================
# 源事件摄入：按明确的批次 ID 与维度计数写入，保证取数口径可复现
# ============================================================================

def ingest_batch_events(batch_id: str, region_counts: dict[str, int],
                        channel_counts: dict[str, int]) -> dict[str, Any]:
    """摄入一个源事件批次。

    region_counts / channel_counts 为 {维度取值: 事件条数}，
    每条事件写入 events 表并带上同一 batch_id，供聚合组固定引用。
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[tuple[Any, ...]] = []

    for value, count in region_counts.items():
        for _ in range(int(count)):
            attrs = json.dumps({"region": value}, ensure_ascii=False)
            rows.append(("成组聚合事件", attrs, "地区维度源事件", "cube_builder", now, batch_id, len(attrs)))

    for value, count in channel_counts.items():
        for _ in range(int(count)):
            attrs = json.dumps({"channel": value}, ensure_ascii=False)
            rows.append(("成组聚合事件", attrs, "渠道维度源事件", "cube_builder", now, batch_id, len(attrs)))

    if rows:
        db.executemany(
            "INSERT INTO events(event_name,event_attrs,trigger_condition,source_module,"
            "occurred_at,batch_id,payload_size) VALUES(?,?,?,?,?,?,?)",
            rows,
        )
    return {"batch_id": batch_id, "ingested": len(rows),
            "region_events": sum(region_counts.values()),
            "channel_events": sum(channel_counts.values())}


# ============================================================================
# 聚合组：创建时快照源事件批次
# ============================================================================

def _load_batch_ids(batch_ids: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    for bid in batch_ids:
        if bid and bid not in cleaned:
            cleaned.append(bid)
    return cleaned


def create_aggregation_group(group_code: str, group_name: str,
                             batch_ids: Sequence[str]) -> dict[str, Any]:
    """创建一次提交的聚合组，固定同一批源事件，并初始化两条数据集记录。"""
    snapshot_batches = _load_batch_ids(batch_ids)
    if not snapshot_batches:
        raise ValueError("至少需要固定一个源事件批次")

    existing = db.fetchone("SELECT id FROM aggregation_groups WHERE group_code=?", [group_code])
    if existing is not None:
        raise ValueError(f"聚合组编码 {group_code} 已存在")

    placeholders = ",".join("?" for _ in snapshot_batches)
    event_count = db.count(
        "events", f"batch_id IN ({placeholders})", snapshot_batches,
    )

    group_id = db.insert("aggregation_groups", {
        "group_code": group_code,
        "group_name": group_name,
        "event_batch_ids": json.dumps(snapshot_batches, ensure_ascii=False),
        "event_count": event_count,
        "status": GROUP_STATUS_PENDING,
    })

    for spec in DATASET_SPECS:
        db.insert("aggregation_group_datasets", {
            "group_id": group_id,
            "dataset_key": spec["dataset_key"],
            "dimension_name": spec["dimension_name"],
            "status": STATUS_PENDING,
            "event_batch_ids": json.dumps(snapshot_batches, ensure_ascii=False),
            "dataset_version": 1,
        })

    return get_group(group_id)


def _events_of_group(group: dict[str, Any]) -> list[dict[str, Any]]:
    """只读取聚合组固定批次内的源事件——固定同一批源事件的关键。"""
    batch_ids = json.loads(group["event_batch_ids"]) if isinstance(
        group["event_batch_ids"], str) else group["event_batch_ids"]
    if not batch_ids:
        return []
    placeholders = ",".join("?" for _ in batch_ids)
    return db.fetchall(
        f"SELECT * FROM events WHERE batch_id IN ({placeholders})", batch_ids,
    )


def _aggregate_dimension(events: list[dict[str, Any]], dimension: str) -> tuple[float, list[dict[str, Any]]]:
    """对固定事件按单个维度计数，返回总值与维度明细。"""
    counts: dict[str, int] = {}
    for evt in events:
        try:
            attrs = json.loads(evt.get("event_attrs", "{}")) if isinstance(
                evt.get("event_attrs"), str) else evt.get("event_attrs", {})
        except (json.JSONDecodeError, TypeError):
            attrs = {}
        if dimension not in attrs:
            # 该事件不携带此维度（例如渠道事件没有 region），不计入该数据集
            continue
        value = str(attrs.get(dimension))
        counts[value] = counts.get(value, 0) + 1

    cells = [{"dimension_value": value, "cell_value": float(cnt)}
             for value, cnt in sorted(counts.items())]
    total = float(sum(counts.values()))
    return total, cells


# ============================================================================
# 批量聚合：逐数据集计算，支持部分失败
# ============================================================================

def _compute_one_dataset(group: dict[str, Any], dataset_key: str,
                         fail: bool = False, fail_reason: str = "") -> dict[str, Any]:
    """计算单个数据集并写入完成记录；fail=True 时模拟该数据集计算失败。"""
    record = db.fetchone(
        "SELECT * FROM aggregation_group_datasets WHERE group_id=? AND dataset_key=?",
        [group["id"], dataset_key],
    )
    if record is None:
        raise ValueError(f"聚合组 {group['group_code']} 不存在数据集 {dataset_key}")

    # 已完成记录不可变：任何重跑/补做都不得覆盖
    if record["status"] == STATUS_COMPLETED:
        return _serialize_dataset(record, skipped=True)

    batch_ids = json.loads(record["event_batch_ids"]) if isinstance(
        record["event_batch_ids"], str) else record["event_batch_ids"]

    if fail:
        db.update("aggregation_group_datasets", {
            "status": STATUS_FAILED,
            "error_message": fail_reason or "模拟部分计算失败：结果未写出",
            "computed_at": db.now(),
        }, "id=?", [record["id"]])
        return _serialize_dataset(db.fetchone(
            "SELECT * FROM aggregation_group_datasets WHERE id=?", [record["id"]]))

    events = _events_of_group(group)
    total, cells = _aggregate_dimension(events, record["dimension_name"])

    # 同一记录先清旧明细再写，保证补写成功后明细与完成记录一致
    db.delete("aggregation_dataset_cells", "dataset_record_id=?", [record["id"]])
    for cell in cells:
        db.insert("aggregation_dataset_cells", {
            "dataset_record_id": record["id"],
            "group_id": group["id"],
            "dimension_value": cell["dimension_value"],
            "cell_value": cell["cell_value"],
        })

    db.update("aggregation_group_datasets", {
        "status": STATUS_COMPLETED,
        "measure_value": total,
        "event_batch_ids": json.dumps(batch_ids, ensure_ascii=False),
        "dataset_version": int(record["dataset_version"]),
        "error_message": None,
        "computed_at": db.now(),
    }, "id=?", [record["id"]])

    return _serialize_dataset(db.fetchone(
        "SELECT * FROM aggregation_group_datasets WHERE id=?", [record["id"]]))


def run_group_aggregation(group_id: int,
                          fail_dataset_keys: Sequence[str] | None = None) -> dict[str, Any]:
    """对整组执行一次批量聚合，逐条记录每个数据集的完成数据。

    fail_dataset_keys 中的数据集模拟计算失败（不写结果），用于部分失败场景。
    """
    group = get_group(group_id)
    if group is None:
        raise ValueError(f"聚合组 {group_id} 不存在")

    fail_set = set(fail_dataset_keys or [])
    unknown = fail_set - set(DATASET_KEYS)
    if unknown:
        raise ValueError(f"未知数据集：{sorted(unknown)}")

    db.update("aggregation_groups", {"status": GROUP_STATUS_RUNNING}, "id=?", [group_id])

    results: list[dict[str, Any]] = []
    for spec in DATASET_SPECS:
        key = spec["dataset_key"]
        should_fail = key in fail_set
        record = db.fetchone(
            "SELECT * FROM aggregation_group_datasets WHERE group_id=? AND dataset_key=?",
            [group_id, key],
        )
        if record["status"] == STATUS_COMPLETED:
            # 整组重跑也不允许覆盖已完成数据集
            results.append(_serialize_dataset(record, skipped=True))
            continue
        results.append(_compute_one_dataset(
            group, key, fail=should_fail,
            fail_reason="模拟部分计算失败：渠道结果未写出" if key == DATASET_CHANNEL else "模拟部分计算失败",
        ))

    refresh_group_status(group_id)
    return {"group": get_group(group_id), "dataset_results": results}


def backfill_group(group_id: int) -> dict[str, Any]:
    """原组补做：只跑缺失项（待计算/计算失败），已完成数据集原样保留。"""
    group = get_group(group_id)
    if group is None:
        raise ValueError(f"聚合组 {group_id} 不存在")

    records = db.fetchall(
        "SELECT * FROM aggregation_group_datasets WHERE group_id=? ORDER BY id", [group_id]
    )
    results: list[dict[str, Any]] = []
    for record in records:
        if record["status"] == STATUS_COMPLETED:
            # 已完成（如地区 90）不可被补跑覆盖
            results.append(_serialize_dataset(record, skipped=True))
            continue
        results.append(_compute_one_dataset(group, record["dataset_key"]))

    refresh_group_status(group_id)
    return {"group": get_group(group_id), "dataset_results": results}


def refresh_group_status(group_id: int) -> str:
    """根据组内数据集状态推导整组状态并写回（状态只在服务/管道层流转）。"""
    records = db.fetchall(
        "SELECT status FROM aggregation_group_datasets WHERE group_id=?", [group_id]
    )
    statuses = [r["status"] for r in records]
    if statuses and all(s == STATUS_COMPLETED for s in statuses):
        new_status = GROUP_STATUS_COMPLETED
        db.update("aggregation_groups",
                  {"status": new_status, "completed_at": db.now()}, "id=?", [group_id])
    elif any(s == STATUS_COMPLETED for s in statuses):
        new_status = GROUP_STATUS_PARTIAL
        db.update("aggregation_groups", {"status": new_status}, "id=?", [group_id])
    elif any(s == STATUS_FAILED for s in statuses):
        new_status = GROUP_STATUS_RUNNING
        db.update("aggregation_groups", {"status": new_status}, "id=?", [group_id])
    else:
        new_status = GROUP_STATUS_PENDING
    return new_status


# ============================================================================
# 查询：组、数据集、维度明细
# ============================================================================

def _serialize_dataset(record: dict[str, Any], skipped: bool = False) -> dict[str, Any]:
    batch_ids = json.loads(record["event_batch_ids"]) if isinstance(
        record.get("event_batch_ids"), str) else record.get("event_batch_ids", [])
    cells = db.fetchall(
        "SELECT dimension_value, cell_value FROM aggregation_dataset_cells "
        "WHERE dataset_record_id=? ORDER BY dimension_value",
        [record["id"]],
    )
    return {
        "id": record["id"],
        "group_id": record["group_id"],
        "dataset_key": record["dataset_key"],
        "dimension_name": record["dimension_name"],
        "label": next((s["label"] for s in DATASET_SPECS
                       if s["dataset_key"] == record["dataset_key"]), record["dataset_key"]),
        "status": record["status"],
        "measure_value": record["measure_value"],
        "event_batch_ids": batch_ids,
        "dataset_version": record["dataset_version"],
        "error_message": record["error_message"],
        "computed_at": record["computed_at"],
        "cells": cells,
        "skipped_completed": skipped,
    }


def list_datasets(group_id: int) -> list[dict[str, Any]]:
    records = db.fetchall(
        "SELECT * FROM aggregation_group_datasets WHERE group_id=? ORDER BY id", [group_id]
    )
    return [_serialize_dataset(r) for r in records]


def get_group(group_id: int) -> dict[str, Any] | None:
    group = db.fetchone("SELECT * FROM aggregation_groups WHERE id=?", [group_id])
    if group is None:
        return None
    group = dict(group)
    group["event_batch_ids"] = json.loads(group["event_batch_ids"]) if isinstance(
        group["event_batch_ids"], str) else group["event_batch_ids"]
    group["datasets"] = list_datasets(group_id)
    completed = [d for d in group["datasets"] if d["status"] == STATUS_COMPLETED]
    group["completed_count"] = len(completed)
    group["total_datasets"] = len(group["datasets"])
    return group


def list_groups() -> list[dict[str, Any]]:
    groups = db.fetchall("SELECT id FROM aggregation_groups ORDER BY id")
    return [get_group(r["id"]) for r in groups]


def get_latest_group_id() -> int | None:
    row = db.fetchone("SELECT MAX(id) as mid FROM aggregation_groups")
    return row["mid"] if row and row["mid"] is not None else None
