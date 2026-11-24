"""仪表盘构建器领域服务：构建和配置数据可视化仪表盘。

真实数据流：配置仪表盘（图表配置/刷新间隔/数据范围）
→ 按刷新间隔从 aggregation_results 和 metric_values 拉取数据
→ 拼装为仪表盘数据 → 写入 dashboards 表。

成组批量聚合接入后，地区图 / 地区渠道联图只读取聚合组内「已完成」的数据集记录，
每个图表都回传所属聚合组、数据集版本与固定的源事件批次，可逐项核对取数版本；
联图要求两条数据集同属一个聚合组，因此不可能拼出两次提交的结果。
"""
from __future__ import annotations

import json
from typing import Any

from app import batch_aggregation as ba
from app.database import db
from app.services.base import BaseModuleService


class DashboardBuilderService(BaseModuleService):
    MODULE = "dashboard_builder"
    ENTITY = "仪表盘"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["仪表盘名称", "包含图表", "刷新间隔", "数据范围", "共享状态"]
    REQUIRED_FIELDS = ["仪表盘名称", "刷新间隔"]
    STATUS_ORDER = ["草稿", "已发布", "已归档"]
    ACTION_RULES = {"发布面板": "已发布", "归档面板": "已归档"}
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 成组批量聚合图表：地区图 / 地区渠道联图
    # ------------------------------------------------------------------

    def _resolve_group(self, group_id: int | None) -> dict[str, Any] | None:
        if group_id is None:
            group_id = ba.get_latest_group_id()
        if group_id is None:
            return None
        return ba.get_group(group_id)

    @staticmethod
    def _chart_provenance(dataset: dict[str, Any], group: dict[str, Any]) -> dict[str, Any]:
        """图表的版本核对信息：用的是哪个数据集、哪个版本、哪批源事件。"""
        return {
            "group_id": group["id"],
            "group_code": group["group_code"],
            "group_name": group["group_name"],
            "group_status": group["status"],
            "dataset_record_id": dataset["id"],
            "dataset_key": dataset["dataset_key"],
            "dataset_label": dataset["label"],
            "dataset_version": dataset["dataset_version"],
            "event_batch_ids": dataset["event_batch_ids"],
            "computed_at": dataset["computed_at"],
        }

    def region_chart(self, group_id: int | None = None) -> dict[str, Any]:
        """地区图：读取指定组（默认最新组）的地区数据集完成记录。"""
        group = self._resolve_group(group_id)
        if group is None:
            return {"ok": False, "message": "暂无聚合组，无法读取地区图", "chart_type": "region"}

        dataset = next((d for d in group["datasets"]
                        if d["dataset_key"] == ba.DATASET_REGION), None)
        if dataset is None or dataset["status"] != ba.STATUS_COMPLETED:
            return {
                "ok": False,
                "chart_type": "region",
                "title": "地区图",
                "message": "地区数据集尚未完成，暂无可核对的完成数据",
                "group": {"group_id": group["id"], "group_code": group["group_code"],
                          "group_status": group["status"]},
            }

        return {
            "ok": True,
            "chart_type": "region",
            "title": "地区图",
            "data": [{"dimension_value": c["dimension_value"], "value": c["cell_value"]}
                     for c in dataset["cells"]],
            "total": dataset["measure_value"],
            "provenance": self._chart_provenance(dataset, group),
        }

    def region_channel_chart(self, group_id: int | None = None) -> dict[str, Any]:
        """地区渠道联图：仅当同组两条数据集都完成时才出数。

        两条完成记录必须属于同一个聚合组；任一条缺失或来自不同组都拒绝拼装，
        从结构上保证联图不能拼出两次提交的结果。
        """
        group = self._resolve_group(group_id)
        if group is None:
            return {"ok": False, "message": "暂无聚合组，无法读取地区渠道联图",
                    "chart_type": "region_channel"}

        by_key = {d["dataset_key"]: d for d in group["datasets"]}
        region_ds = by_key.get(ba.DATASET_REGION)
        channel_ds = by_key.get(ba.DATASET_CHANNEL)

        missing = [d["label"] for d in (region_ds, channel_ds)
                   if d is None or d["status"] != ba.STATUS_COMPLETED]
        if missing:
            return {
                "ok": False,
                "chart_type": "region_channel",
                "title": "地区渠道联图",
                "message": f"同组完成数据不足，拒绝跨提交拼装；缺失/未完成：{'、'.join(missing)}",
                "group": {"group_id": group["id"], "group_code": group["group_code"],
                          "group_status": group["status"]},
                "datasets": [
                    {"dataset_key": ba.DATASET_REGION,
                     "status": region_ds["status"] if region_ds else "缺失"},
                    {"dataset_key": ba.DATASET_CHANNEL,
                     "status": channel_ds["status"] if channel_ds else "缺失"},
                ],
            }

        # 双保险：批次快照必须一致（同一次提交固定的同一批源事件）
        if region_ds["event_batch_ids"] != channel_ds["event_batch_ids"]:
            return {
                "ok": False,
                "chart_type": "region_channel",
                "title": "地区渠道联图",
                "message": "两条数据集源事件批次不一致，拒绝跨提交拼装",
                "group": {"group_id": group["id"], "group_code": group["group_code"]},
            }

        return {
            "ok": True,
            "chart_type": "region_channel",
            "title": "地区渠道联图",
            "region": {
                "total": region_ds["measure_value"],
                "data": [{"dimension_value": c["dimension_value"], "value": c["cell_value"]}
                         for c in region_ds["cells"]],
                "provenance": self._chart_provenance(region_ds, group),
            },
            "channel": {
                "total": channel_ds["measure_value"],
                "data": [{"dimension_value": c["dimension_value"], "value": c["cell_value"]}
                         for c in channel_ds["cells"]],
                "provenance": self._chart_provenance(channel_ds, group),
            },
            "shared_provenance": {
                "group_id": group["id"],
                "group_code": group["group_code"],
                "group_name": group["group_name"],
                "event_batch_ids": region_ds["event_batch_ids"],
                "dataset_versions": {
                    ba.DATASET_REGION: region_ds["dataset_version"],
                    ba.DATASET_CHANNEL: channel_ds["dataset_version"],
                },
            },
        }

    def batch_dashboard(self, group_id: int | None = None) -> dict[str, Any]:
        """仪表盘批量聚合视图：地区图 + 联图，均与实际数据集和源事件批次关联。"""
        group = self._resolve_group(group_id)
        return {
            "ok": True,
            "group": group,
            "region_chart": self.region_chart(group_id),
            "region_channel_chart": self.region_channel_chart(group_id),
        }

    # ------------------------------------------------------------------
    # 原有仪表盘数据
    # ------------------------------------------------------------------

    def dashboard_data(self, dashboard_id: int) -> dict[str, Any]:
        """组装仪表盘的实时数据。"""
        dashboard = db.fetchone("SELECT * FROM dashboards WHERE id=?", [dashboard_id])
        if dashboard is None:
            return {"ok": False, "message": "仪表盘不存在"}

        chart_configs_raw = dashboard.get("chart_configs", "[]")
        try:
            charts = json.loads(chart_configs_raw) if isinstance(chart_configs_raw, str) else chart_configs_raw
        except (json.JSONDecodeError, TypeError):
            charts = []

        chart_data = []

        # 默认仪表盘数据：拉取最新聚合和指标
        if not charts or len(charts) == 0:
            agg_values = db.fetchall(
                "SELECT aw.window_name, ar.computed_value, ar.dimension_key "
                "FROM aggregation_results ar JOIN aggregation_windows aw ON ar.window_id=aw.id "
                "ORDER BY ar.window_start DESC LIMIT 50"
            )
            metric_values = db.fetchall(
                "SELECT m.metric_name, mv.snapshot_value "
                "FROM metric_values mv JOIN metrics m ON mv.metric_id=m.id "
                "ORDER BY mv.snapshot_time DESC LIMIT 20"
            )

            chart_data = [
                {
                    "chart_type": "line",
                    "title": "聚合趋势",
                    "data": [{"x": r["dimension_key"], "y": r["computed_value"]} for r in agg_values[:20]],
                },
                {
                    "chart_type": "bar",
                    "title": "指标快照",
                    "data": [{"label": r["metric_name"], "value": r["snapshot_value"]} for r in metric_values],
                },
            ]
        else:
            for chart in charts:
                chart_type = chart.get("type", "line")
                chart_title = chart.get("title", "图表")
                module = chart.get("module", "events")

                if module == "aggregation":
                    rows = db.fetchall(
                        "SELECT * FROM aggregation_results ORDER BY window_start DESC LIMIT 30"
                    )
                elif module == "metrics":
                    rows = db.fetchall(
                        "SELECT mv.snapshot_value, mv.snapshot_time, m.metric_name "
                        "FROM metric_values mv JOIN metrics m ON mv.metric_id=m.id "
                        "ORDER BY mv.snapshot_time DESC LIMIT 30"
                    )
                else:
                    rows = db.fetchall("SELECT event_name as name, COUNT(*) as cnt FROM events GROUP BY event_name LIMIT 20")

                chart_data.append({"chart_type": chart_type, "title": chart_title, "data": rows})

        return {
            "ok": True,
            "dashboard_name": dashboard["dashboard_name"],
            "refresh_interval": dashboard.get("refresh_interval_seconds", 30),
            "charts": chart_data,
        }

    def publish(self, dashboard_id: int) -> dict[str, Any]:
        """发布仪表盘。"""
        db.update("dashboards", {"status": "已发布"}, "id=?", [dashboard_id])
        return self.dashboard_data(dashboard_id)

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_dashboards"] = db.count("dashboards")
        snapshot["published"] = db.count("dashboards", "status='已发布'")
        return snapshot
