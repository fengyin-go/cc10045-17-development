/** 成组批量聚合 API：一次提交固定同一批源事件，逐条产出地区/渠道数据集。 */
import { request } from './client'

export type DatasetRecord = {
  id: number
  group_id: number
  dataset_key: 'region' | 'channel'
  dimension_name: string
  label: string
  status: string
  measure_value: number | null
  event_batch_ids: string[]
  dataset_version: number
  error_message: string | null
  computed_at: string | null
  cells: { dimension_value: string; cell_value: number }[]
  skipped_completed?: boolean
}

export type BatchGroup = {
  id: number
  group_code: string
  group_name: string
  event_batch_ids: string[]
  event_count: number
  status: string
  completed_at: string | null
  completed_count: number
  total_datasets: number
  datasets: DatasetRecord[]
}

export type Provenance = {
  group_id: number
  group_code: string
  group_name: string
  group_status?: string
  dataset_record_id: number
  dataset_key: string
  dataset_label: string
  dataset_version: number
  event_batch_ids: string[]
  computed_at: string | null
}

export type RegionChart = {
  ok: boolean
  chart_type: string
  title?: string
  message?: string
  total?: number
  data?: { dimension_value: string; value: number }[]
  provenance?: Provenance
  group?: { group_id: number; group_code: string; group_status: string }
}

export type ComboChart = {
  ok: boolean
  chart_type: string
  title?: string
  message?: string
  region?: {
    total: number
    data: { dimension_value: string; value: number }[]
    provenance: Provenance
  }
  channel?: {
    total: number
    data: { dimension_value: string; value: number }[]
    provenance: Provenance
  }
  shared_provenance?: {
    group_id: number
    group_code: string
    group_name: string
    event_batch_ids: string[]
    dataset_versions: Record<string, number>
  }
  datasets?: { dataset_key: string; status: string }[]
  group?: { group_id: number; group_code: string; group_status: string }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null
    throw new Error(body?.detail ? `请求被拒绝：${body.detail}` : `接口返回 ${response.status}`)
  }
  return (await response.json()) as T
}

const BASE = '/api/batch-aggregation'

export function listGroups(): Promise<{ ok: boolean; items: BatchGroup[] }> {
  return call(`${BASE}/groups`)
}

export function createGroup(payload: {
  group_code: string
  group_name: string
  batch_ids: string[]
}): Promise<{ ok: boolean; item: BatchGroup }> {
  return call(`${BASE}/groups`, { method: 'POST', body: JSON.stringify(payload) })
}

export function runGroup(
  groupId: number,
  failDatasetKeys: string[] = [],
): Promise<{ ok: boolean; group: BatchGroup; dataset_results: DatasetRecord[] }> {
  return call(`${BASE}/groups/${groupId}/run`, {
    method: 'POST',
    body: JSON.stringify({ fail_dataset_keys: failDatasetKeys }),
  })
}

export function backfillGroup(
  groupId: number,
): Promise<{ ok: boolean; group: BatchGroup; dataset_results: DatasetRecord[] }> {
  return call(`${BASE}/groups/${groupId}/backfill`, { method: 'POST', body: '{}' })
}

export function ingestBatch(payload: {
  batch_id: string
  region_counts: Record<string, number>
  channel_counts: Record<string, number>
}): Promise<{ ok: boolean; result: { batch_id: string; ingested: number } }> {
  return call(`${BASE}/events/batch`, { method: 'POST', body: JSON.stringify(payload) })
}

export function listBatches(): Promise<{
  ok: boolean
  items: { batch_id: string; event_count: number }[]
}> {
  return call(`${BASE}/events/batches`)
}

export function regionChart(groupId?: number): Promise<RegionChart> {
  const query = groupId ? `?group_id=${groupId}` : ''
  return call(`${BASE}/dashboard/region${query}`)
}

export function regionChannelChart(groupId?: number): Promise<ComboChart> {
  const query = groupId ? `?group_id=${groupId}` : ''
  return call(`${BASE}/dashboard/region-channel${query}`)
}

export function loadScenario(): Promise<{
  ok: boolean
  message: string
  old_group_id: number
  new_group_id: number
}> {
  return call(`${BASE}/scenario/load`, { method: 'POST', body: '{}' })
}
