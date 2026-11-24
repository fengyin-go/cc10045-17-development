<template>
  <section class="batch-panel">
    <header class="page-head">
      <div>
        <h3>地区/渠道成组批量聚合</h3>
        <p class="page-desc">
          一次提交固定同一批源事件（批次快照），逐条记录地区、渠道数据集的完成数据与版本；
          补做只跑缺失项，已完成数据不可覆盖。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="load-scenario" @click="onLoadScenario">
          装载验收场景
        </button>
        <button class="btn ghost" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="message" class="error-text" :class="{ 'ok-text': !hasError }">{{ message }}</p>

    <div class="form-grid">
      <form class="analysis-form" @submit.prevent="onIngest">
        <strong>摄入源事件批次</strong>
        <label>批次 ID
          <input v-model="ingestForm.batch_id" data-testid="ingest-batch-id" required placeholder="如 BATCH_2026_11" />
        </label>
        <label>地区计数 JSON
          <input v-model="ingestForm.region_counts" placeholder='{"华东":40,"华南":30}' />
        </label>
        <label>渠道计数 JSON
          <input v-model="ingestForm.channel_counts" placeholder='{"app":45,"web":35}' />
        </label>
        <button class="btn primary" type="submit" :disabled="busy">摄入批次</button>
      </form>

      <form class="analysis-form" @submit.prevent="onCreateGroup">
        <strong>新增一次提交（聚合组）</strong>
        <label>聚合组编码
          <input v-model="groupForm.group_code" data-testid="group-code" required placeholder="如 GRP_2026_11" />
        </label>
        <label>聚合组名称
          <input v-model="groupForm.group_name" required placeholder="如 双11批次聚合" />
        </label>
        <label>固定源事件批次（逗号分隔）
          <input v-model="groupForm.batch_ids" data-testid="group-batches" required placeholder="BATCH_2026_11" />
        </label>
        <button class="btn primary" type="submit" :disabled="busy">提交并固定批次</button>
      </form>
    </div>

    <table class="data-table" data-testid="group-table">
      <thead>
        <tr>
          <th>聚合组</th>
          <th>固定源事件批次</th>
          <th>整组状态</th>
          <th>地区数据集</th>
          <th>渠道数据集</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="group in groups" :key="group.id" :data-testid="`group-row-${group.id}`">
          <td>
            <strong>{{ group.group_code }}</strong>
            <div class="muted">{{ group.group_name }}</div>
            <div class="muted">事件 {{ group.event_count }} 条 · 完成 {{ group.completed_count }}/{{ group.total_datasets }}</div>
          </td>
          <td><code>{{ group.event_batch_ids.join(', ') }}</code></td>
          <td><span class="status-pill" :class="statusClass(group.status)">{{ group.status }}</span></td>
          <td v-for="ds in group.datasets" :key="ds.dataset_key">
            <div class="ds-block">
              <span class="status-pill" :class="statusClass(ds.status)">{{ ds.status }}</span>
              <template v-if="ds.status === '已完成'">
                <div class="ds-value">值：<strong>{{ ds.measure_value }}</strong></div>
                <div class="muted">版本 v{{ ds.dataset_version }} · 记录 #{{ ds.id }}</div>
                <div class="muted">批次：{{ ds.event_batch_ids.join(', ') }}</div>
              </template>
              <div v-else-if="ds.error_message" class="error-text">{{ ds.error_message }}</div>
            </div>
          </td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`run-${group.id}`" @click="onRun(group.id, [])">
              整组计算
            </button>
            <button class="link" type="button" :data-testid="`run-fail-channel-${group.id}`"
                    @click="onRun(group.id, ['channel'])">
              模拟渠道失败
            </button>
            <button class="link" type="button" :data-testid="`backfill-${group.id}`" @click="onBackfill(group.id)">
              原组补做
            </button>
          </td>
        </tr>
        <tr v-if="!groups.length">
          <td colspan="6" class="empty-state">暂无聚合组，可先装载验收场景或提交一次新聚合</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  backfillGroup,
  createGroup,
  ingestBatch,
  listGroups,
  loadScenario,
  runGroup,
  type BatchGroup,
} from '@/api/batch-aggregation'

const groups = ref<BatchGroup[]>([])
const busy = ref(false)
const message = ref('')
const hasError = ref(false)

const ingestForm = ref({ batch_id: '', region_counts: '', channel_counts: '' })
const groupForm = ref({ group_code: '', group_name: '', batch_ids: '' })

function parseCounts(raw: string): Record<string, number> {
  const text = raw.trim()
  if (!text) return {}
  const parsed = JSON.parse(text) as Record<string, unknown>
  const result: Record<string, number> = {}
  for (const [key, value] of Object.entries(parsed)) {
    result[key] = Number(value)
  }
  return result
}

function notify(text: string, isError = false) {
  message.value = text
  hasError.value = isError
}

function statusClass(status: string): string {
  if (status === '已完成' || status === '整组完成') return 'st-ok'
  if (status === '计算失败') return 'st-fail'
  if (status === '部分完成' || status === '计算中') return 'st-warn'
  return 'st-pending'
}

async function reload() {
  try {
    const payload = await listGroups()
    groups.value = payload.items
  } catch (error) {
    notify(error instanceof Error ? error.message : '聚合组读取失败', true)
  }
}

async function onIngest() {
  busy.value = true
  try {
    const result = await ingestBatch({
      batch_id: ingestForm.value.batch_id.trim(),
      region_counts: parseCounts(ingestForm.value.region_counts),
      channel_counts: parseCounts(ingestForm.value.channel_counts),
    })
    notify(`批次 ${result.result.batch_id} 已摄入 ${result.result.ingested} 条源事件`)
    ingestForm.value = { batch_id: '', region_counts: '', channel_counts: '' }
  } catch (error) {
    notify(error instanceof Error ? error.message : '批次摄入失败', true)
  } finally {
    busy.value = false
  }
}

async function onCreateGroup() {
  busy.value = true
  try {
    const batchIds = groupForm.value.batch_ids
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    const result = await createGroup({
      group_code: groupForm.value.group_code.trim(),
      group_name: groupForm.value.group_name.trim(),
      batch_ids: batchIds,
    })
    notify(`聚合组 ${result.item.group_code} 已创建，固定批次：${result.item.event_batch_ids.join(', ')}`)
    groupForm.value = { group_code: '', group_name: '', batch_ids: '' }
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '聚合组创建失败', true)
  } finally {
    busy.value = false
  }
}

async function onRun(groupId: number, failKeys: string[]) {
  busy.value = true
  try {
    const result = await runGroup(groupId, failKeys)
    const summary = result.dataset_results
      .map((ds) => `${ds.label}=${ds.status}${ds.measure_value !== null ? `(${ds.measure_value})` : ''}`)
      .join('，')
    notify(`整组计算完成（组状态：${result.group.status}）：${summary}`)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '整组计算失败', true)
  } finally {
    busy.value = false
  }
}

async function onBackfill(groupId: number) {
  busy.value = true
  try {
    const result = await backfillGroup(groupId)
    const summary = result.dataset_results
      .map((ds) => `${ds.label}${ds.skipped_completed ? '保留已完成' : '补算完成'}=${ds.measure_value}`)
      .join('，')
    notify(`原组补做完成（组状态：${result.group.status}）：${summary}`)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '补做失败', true)
  } finally {
    busy.value = false
  }
}

async function onLoadScenario() {
  busy.value = true
  try {
    const result = await loadScenario()
    notify(result.message)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '场景装载失败', true)
  } finally {
    busy.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.batch-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 20px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.analysis-form {
  margin: 0;
}
.analysis-form label {
  flex: 1 1 100%;
}
.muted {
  color: var(--muted);
  font-size: 12px;
}
.ds-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ds-value {
  font-size: 14px;
}
.status-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  width: fit-content;
}
.st-ok {
  background: #dcfce7;
  color: #166534;
}
.st-fail {
  background: #fee2e2;
  color: #991b1b;
}
.st-warn {
  background: #fef3c7;
  color: #92400e;
}
.st-pending {
  background: #e2e8f0;
  color: #475569;
}
.ok-text {
  color: #166534;
}
code {
  font-size: 12px;
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 4px;
}
</style>
