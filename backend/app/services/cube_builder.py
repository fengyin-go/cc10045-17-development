"""多维数据集构建领域服务：定义多维Cube并执行预计算。

真实数据流：定义Cube（事实表/维度组合/指标计算）
→ pipeline.precompute_cube 从 events 表按维度组合聚合
→ 写入 cube_cells → 下游仪表盘查询加速。

成组批量聚合：一次提交创建聚合组并固定同一批源事件，
逐条产出地区/渠道数据集完成记录，支持部分失败后的原组补做（只跑缺失项）。
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from app import batch_aggregation as ba
from app.database import db
from app.pipeline import precompute_cube
from app.services.base import BaseModuleService


class CubeBuilderService(BaseModuleService):
    MODULE = "cube_builder"
    ENTITY = "Cube定义"
    OPERATION_TERM = "预计算"
    LIST_FIELDS = ["Cube名称", "事实表", "维度组合", "指标计算", "预计算状态"]
    REQUIRED_FIELDS = ["Cube名称", "事实表", "维度组合", "指标计算"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用Cube": "已启用", "停用Cube": "已停用"}
    NEGATIVE_ACTIONS = []

    # ================================================================
    # 成组批量聚合（地区/渠道数据集）
    # ================================================================

    def ingest_source_batch(self, batch_id: str, region_counts: dict[str, int],
                            channel_counts: dict[str, int]) -> dict[str, Any]:
        """摄入一批源事件，返回批次摘要。"""
        return ba.ingest_batch_events(batch_id, region_counts, channel_counts)

    def create_batch_group(self, group_code: str, group_name: str,
                           batch_ids: Sequence[str]) -> dict[str, Any]:
        """新增一次提交的地区/渠道批量聚合组，固定同一批源事件。"""
        return ba.create_aggregation_group(group_code, group_name, batch_ids)

    def run_batch_group(self, group_id: int,
                        fail_dataset_keys: Sequence[str] | None = None) -> dict[str, Any]:
        """执行整组批量聚合，逐条记录完成数据；可模拟部分数据集计算失败。"""
        return ba.run_group_aggregation(group_id, fail_dataset_keys=fail_dataset_keys)

    def backfill_batch_group(self, group_id: int) -> dict[str, Any]:
        """原组补做：只跑缺失项，已完成数据集不被覆盖。"""
        return ba.backfill_group(group_id)

    def list_batch_groups(self) -> list[dict[str, Any]]:
        return ba.list_groups()

    def get_batch_group(self, group_id: int) -> dict[str, Any] | None:
        return ba.get_group(group_id)

    def run_precomputation(self, cube_id: int) -> dict[str, Any]:
        """执行Cube预计算。"""
        cube = db.fetchone("SELECT * FROM cubes WHERE id=?", [cube_id])
        if cube is None:
            return {"ok": False, "message": f"Cube {cube_id} 不存在"}

        db.update("cubes", {"precomputation_status": "计算中"}, "id=?", [cube_id])
        cell_count = precompute_cube(dict(cube))

        return {
            "ok": True,
            "cube_name": cube["cube_name"],
            "cells_computed": cell_count,
            "status": "已启用",
        }

    def cube_cells(self, cube_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """获取Cube预计算结果。"""
        rows = db.fetchall(
            "SELECT * FROM cube_cells WHERE cube_id=? ORDER BY measure_value DESC LIMIT ?",
            [cube_id, limit],
        )
        for r in rows:
            try:
                r["dimension_values"] = json.loads(r["dimension_values"]) if isinstance(
                    r["dimension_values"], str) else r["dimension_values"]
            except json.JSONDecodeError:
                pass
        return rows

    def aggregate_by_dimension(self, cube_id: int, dimension: str) -> list[dict[str, Any]]:
        """按指定维度汇总Cube数据。"""
        rows = db.fetchall(
            "SELECT dimension_values, SUM(measure_value) as total FROM cube_cells WHERE cube_id=? GROUP BY 1",
            [cube_id],
        )
        result: list[dict[str, Any]] = []
        for r in rows:
            try:
                dims = json.loads(r["dimension_values"]) if isinstance(r["dimension_values"], str) else {}
                val = dims.get(dimension, "unknown")
            except (json.JSONDecodeError, TypeError):
                val = "parse_error"
            result.append({"dimension_value": val, "total": r["total"]})
        return sorted(result, key=lambda x: -x["total"])

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_cubes"] = db.count("cubes")
        snapshot["total_cells"] = db.count("cube_cells")
        snapshot["enabled_cubes"] = db.count("cubes", "precomputation_status='已启用'")
        snapshot["batch_groups"] = db.count("aggregation_groups")
        snapshot["completed_datasets"] = db.count(
            "aggregation_group_datasets", "status='已完成'")
        return snapshot