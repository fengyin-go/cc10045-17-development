# 实时数据分析与可视化应用平台

支撑实时数据聚合、多维度分析、可视化图表生成和数据应用发布的分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8019/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5199/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8019`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实时聚合器 | `realtime_agg` | 聚合窗口 | 窗口名称、数据源、聚合维度 |
| 事件追踪器 | `event_tracker` | 事件定义 | 事件名称、事件属性、触发条件 |
| 仪表盘构建器 | `dashboard_builder` | 仪表盘 | 仪表盘名称、包含图表、刷新间隔 |
| 查询缓存层 | `query_cache` | 缓存配置 | 配置名称、关联数据集、缓存时长秒数 |
| 分群引擎 | `segment_engine` | 分群定义 | 分群名称、分群条件、所属人口特征 |
| 指标存储 | `metric_store` | 指标快照 | 指标名称、快照值、快照时间 |
| 下钻路径 | `drill_path` | 下钻规则 | 规则名称、起始指标、下钻维度 |
| 分析告警策略 | `alert_policy` | 告警规则 | 规则名称、监控指标、告警条件 |
| 数据报告 | `data_report` | 报告生成器 | 报告名称、包含指标集、报告周期 |
| 异常归因 | `anomaly_analysis` | 归因分析器 | 分析对象、异常时间点、归因维度 |
| 预测模型 | `forecast_model` | 预测任务 | 任务名称、预测指标、预测周期 |
| 分群对比 | `cohort_compare` | 对比报告 | 对比名称、分群A、分群B |
| 分析结果导出 | `data_export` | 导出任务 | 导出名称、查询条件、导出格式 |
| 数据订阅 | `data_subscription` | 订阅任务 | 订阅名称、关联指标、推送条件 |
| 查询历史 | `query_history` | 查询记录 | 查询时间、执行人、涉及数据集 |
| 报告快照 | `report_snapshot` | 快照记录 | 快照名称、所属报告、快照时间 |
| 数据API | `data_api` | 数据接口 | 接口名称、关联指标、请求方式 |
| 留存分析 | `retention_analysis` | 留存任务 | 任务名称、行为事件、留存天数 |
| 漏斗分析器 | `funnel_analyzer` | 漏斗定义 | 漏斗名称、步骤序列、转化窗口小时 |
| 多维数据集构建 | `cube_builder` | Cube定义 | Cube名称、事实表、维度组合 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 地区/渠道成组批量聚合

多维数据集（`cube_builder`）支持一次提交一组**地区/渠道批量聚合**，解决“多次摄入事件后取数口径
串组、部分失败后补跑覆盖已完成数据、联图拼出两次提交结果”的问题。前端在「多维数据集构建」页操作
聚合组，在「仪表盘构建器」页查看带版本核对的图表。

### 数据模型（`app/batch_aggregation.py`）

- `aggregation_groups` 聚合组：一次提交一行，创建时把源事件 `batch_id` 快照到
  `event_batch_ids`，整组后续计算（含补做）只读取这批事件，后摄入的事件不会串入旧组。
- `aggregation_group_datasets` 数据集逐条记录：每组固定两条（`region` 地区、`channel` 渠道），
  各自带 `status / measure_value / event_batch_ids / dataset_version / computed_at`；
  `已完成` 记录不可变。
- `aggregation_dataset_cells` 完成数据的维度明细，随完成记录一次性写入。

### 关键不变量

1. **固定同一批源事件**：组内所有计算只查快照批次内的 `events`。
2. **逐条记录 + 不可覆盖**：补做（backfill）只跑 `待计算/计算失败` 的缺失项，已完成数据集
   原样保留并在返回里标 `skipped_completed=true`。
3. **仪表盘只读完成记录**：地区图/联图均回传 `provenance`（聚合组、数据集记录 id、版本、
   源事件批次、完成时间），可核对这次取数用的是哪个版本。
4. **联图不能跨提交拼装**：两条数据集必须同属一个聚合组且批次快照一致，否则拒绝出数。
5. **后到事件只进新组**：补做前新摄入的事件不会改变旧组结果，只有另开新聚合组固定新批次时
   才会被纳入。

### 接口（前缀 `/api/batch-aggregation`，见 `app/routers/batch_aggregation.py`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/events/batch` | 摄入一批带 `batch_id` 的源事件（地区/渠道维度计数） |
| GET | `/events/batches` | 源事件批次清单 |
| POST | `/groups` | 新增一次提交，固定同一批源事件并初始化两条数据集记录 |
| GET | `/groups`、`/groups/{id}` | 聚合组与逐条数据集状态 |
| POST | `/groups/{id}/run` | 整组批量聚合，可传 `fail_dataset_keys` 模拟部分失败 |
| POST | `/groups/{id}/backfill` | 原组补做：只跑缺失项，不覆盖已完成项 |
| GET | `/dashboard/region?group_id=` | 地区图（默认最新组），带版本/批次核对信息 |
| GET | `/dashboard/region-channel?group_id=` | 地区渠道联图，同组两条齐备才出数 |
| GET | `/dashboard?group_id=` | 成组聚合仪表盘聚合视图 |
| POST | `/scenario/load` | 一键装载验收场景（原组 70/80、新组 90 且渠道失败、后到事件 61） |

领域：数据工程
子方向：数据分析与数据应用
