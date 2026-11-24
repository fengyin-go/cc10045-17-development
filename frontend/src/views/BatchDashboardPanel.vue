<template>
  <section class="batch-panel">
    <header class="page-head">
      <div>
        <h3>成组聚合仪表盘（版本可核对）</h3>
        <p class="page-desc">
          图表只读取聚合组内「已完成」的数据集记录，并回传所属聚合组、数据集版本与固定的源事件批次；
          联图要求两条数据同属一次提交，无法拼出两次提交的结果。
        </p>
      </div>
      <div class="page-actions">
        <label class="group-select">
          聚合组：
          <select v-model="selectedGroupId" data-testid="group-select" @change="loadCharts">
            <option v-for="group in groups" :key="group.id" :value="group.id">
              {{ group.group_code }}（{{ group.status }}）
            </option>
          </select>
        </label>
        <button class="btn ghost" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="message" class="error-text" :class="{ 'ok-text': !hasError }">{{ message }}</p>

    <div class="chart-grid">
      <article class="chart-card" data-testid="region-chart-card">
        <header class="chart-head">
          <h4>地区图</h4>
          <button class="link" type="button" @click="loadRegion">打开地区图</button>
        </header>

        <template v-if="region.ok && region.data">
          <div class="stat-row">
            <article class="stat-card">
              <span class="stat-label">地区事件总量</span>
              <strong class="stat-value">{{ region.total }}</strong>
            </article>
          </div>
          <div class="analysis-chart">
            <div v-for="point in region.data" :key="point.dimension_value" class="analysis-bar"
                 :style="{ height: barHeight(point.value, regionMax) }">
              <span>{{ point.dimension_value }}:{{ point.value }}</span>
            </div>
          </div>
          <ProvenanceBox :prov="region.provenance" />
        </template>
        <p v-else class="empty-state chart-empty">{{ region.message ?? '地区数据集尚未完成' }}</p>
      </article>

      <article class="chart-card" data-testid="combo-chart-card">
        <header class="chart-head">
          <h4>地区渠道联图</h4>
          <button class="link" type="button" @click="loadCombo">打开联图</button>
        </header>

        <template v-if="combo.ok && combo.region && combo.channel">
          <div class="combo-totals">
            <article class="stat-card">
              <span class="stat-label">地区总量</span>
              <strong class="stat-value">{{ combo.region.total }}</strong>
            </article>
            <article class="stat-card">
              <span class="stat-label">渠道总量</span>
              <strong class="stat-value">{{ combo.channel.total }}</strong>
            </article>
          </div>
          <h5>地区维度</h5>
          <div class="analysis-chart small">
            <div v-for="point in combo.region.data" :key="`r-${point.dimension_value}`"
                 class="analysis-bar" :style="{ height: barHeight(point.value, comboRegionMax) }">
              <span>{{ point.dimension_value }}:{{ point.value }}</span>
            </div>
          </div>
          <h5>渠道维度</h5>
          <div class="analysis-chart small">
            <div v-for="point in combo.channel.data" :key="`c-${point.dimension_value}`"
                 class="analysis-bar channel-bar" :style="{ height: barHeight(point.value, comboChannelMax) }">
              <span>{{ point.dimension_value }}:{{ point.value }}</span>
            </div>
          </div>
          <ProvenanceBox :prov="combo.region.provenance" title="地区数据集核对" />
          <ProvenanceBox :prov="combo.channel.provenance" title="渠道数据集核对" />
          <div class="shared-box" data-testid="combo-shared">
            <strong>同一次提交校验：</strong>
            聚合组 {{ combo.shared_provenance?.group_code }} ·
            源事件批次 <code>{{ combo.shared_provenance?.event_batch_ids.join(', ') }}</code> ·
            版本 {{ combo.shared_provenance?.dataset_versions.region }} /
            {{ combo.shared_provenance?.dataset_versions.channel }}
          </div>
        </template>
        <p v-else class="empty-state chart-empty">{{ combo.message ?? '联图数据尚未齐备' }}</p>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'

import {
  listGroups,
  regionChannelChart,
  regionChart,
  type BatchGroup,
  type ComboChart,
  type Provenance,
  type RegionChart,
} from '@/api/batch-aggregation'

// 内联版本核对信息块，避免再拆一个 SFC 文件
const ProvenanceBox = (props: { prov?: Provenance; title?: string }) =>
  h('div', { class: 'provenance-box' }, [
    h('strong', props.title ?? '取数版本核对'),
    h('dl', [
      h('dt', '聚合组'),
      h('dd', `${props.prov?.group_code}（#${props.prov?.group_id}）`),
      h('dt', '数据集记录'),
      h('dd', `${props.prov?.dataset_label} #${props.prov?.dataset_record_id} · v${props.prov?.dataset_version}`),
      h('dt', '源事件批次'),
      h('dd', (props.prov?.event_batch_ids ?? []).join(', ')),
      h('dt', '完成时间'),
      h('dd', props.prov?.computed_at ?? '—'),
    ]),
  ])

const groups = ref<BatchGroup[]>([])
const selectedGroupId = ref<number | undefined>(undefined)
const region = ref<RegionChart>({ ok: false, chart_type: 'region' })
const combo = ref<ComboChart>({ ok: false, chart_type: 'region_channel' })
const message = ref('')
const hasError = ref(false)

const regionMax = computed(() => Math.max(1, ...(region.value.data ?? []).map((p) => p.value)))
const comboRegionMax = computed(() => Math.max(1, ...(combo.value.region?.data ?? []).map((p) => p.value)))
const comboChannelMax = computed(() => Math.max(1, ...(combo.value.channel?.data ?? []).map((p) => p.value)))

function barHeight(value: number, max: number): string {
  return `${Math.max(12, Math.round((value / max) * 130))}px`
}

function notify(text: string, isError = false) {
  message.value = text
  hasError.value = isError
}

async function reload() {
  try {
    const payload = await listGroups()
    groups.value = payload.items
    if (selectedGroupId.value === undefined && groups.value.length) {
      selectedGroupId.value = groups.value[groups.value.length - 1].id
    }
    await loadCharts()
  } catch (error) {
    notify(error instanceof Error ? error.message : '聚合组读取失败', true)
  }
}

async function loadRegion() {
  try {
    region.value = await regionChart(selectedGroupId.value)
  } catch (error) {
    notify(error instanceof Error ? error.message : '地区图读取失败', true)
  }
}

async function loadCombo() {
  try {
    combo.value = await regionChannelChart(selectedGroupId.value)
  } catch (error) {
    notify(error instanceof Error ? error.message : '联图读取失败', true)
  }
}

async function loadCharts() {
  await Promise.all([loadRegion(), loadCombo()])
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
.group-select {
  font-size: 13px;
  color: var(--muted);
}
.group-select select {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 14px;
}
.chart-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  background: #fafbfc;
}
.chart-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.chart-head h4 {
  margin: 0;
}
.chart-empty {
  padding: 24px 8px;
}
.analysis-chart.small {
  height: 120px;
}
.channel-bar {
  background: #7c3aed;
}
.combo-totals {
  display: flex;
  gap: 10px;
}
.combo-totals .stat-card {
  padding: 8px 10px;
}
h5 {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--muted);
}
.provenance-box {
  margin-top: 10px;
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  background: #fff;
  font-size: 12px;
}
.provenance-box dl {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 2px 8px;
  margin: 6px 0 0;
}
.provenance-box dt {
  color: var(--muted);
}
.provenance-box dd {
  margin: 0;
}
.shared-box {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #ecfdf5;
  color: #065f46;
  font-size: 12px;
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
