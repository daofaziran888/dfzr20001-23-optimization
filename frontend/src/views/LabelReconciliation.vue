<template>
  <div class="recon-panel" data-testid="label-recon-panel">
    <header class="recon-head">
      <h4>{{ title }}</h4>
      <button class="btn ghost small" type="button" @click="$emit('close')">关闭</button>
    </header>

    <div v-if="loading" class="recon-loading">核对数据加载中…</div>

    <template v-else>
      <!-- 包装台操作区 -->
      <div v-if="mode === 'packing'" class="recon-actions">
        <button class="btn primary small" type="button" :disabled="busy" @click="onIssue">签发标签留印</button>
        <button class="btn small" type="button" :disabled="busy" @click="openCorrect">更正标签文案</button>
        <button class="btn small" type="button" :disabled="busy" @click="onReprint">重印新标签</button>
        <button class="btn ghost small" type="button" :disabled="busy" @click="load">刷新核对</button>
      </div>

      <!-- 发运台操作区 -->
      <div v-else class="recon-actions">
        <form class="scan-form" @submit.prevent="onScan">
          <input v-model="scanNo" placeholder="输入留印凭据编号，如 LB-BATCH-X-01" />
          <button class="btn primary small" type="button" :disabled="busy" @click="onScan">仓库扫码绑定</button>
        </form>
        <button class="btn ghost small" type="button" :disabled="busy" @click="load">刷新核对</button>
      </div>

      <p v-if="errorText" class="error-text">{{ errorText }}</p>
      <p v-if="infoText" class="info-text">{{ infoText }}</p>

      <!-- 状态摘要 -->
      <section class="recon-summary">
        <span class="chip" :class="data.done ? 'chip-done' : 'chip-pending'">
          {{ data.done ? '已办：已实际发运消费' : '未办：尚无印件被实际发运' }}
        </span>
        <template v-if="mode === 'packing'">
          <span class="chip">标签状态：{{ data.label_status || '未留印' }}</span>
          <span v-if="data.pending_reprint" class="chip chip-warn">文案已更正，等待重印</span>
          <span class="chip">实际出货发运号：{{ data.actual_shipment_no || '尚未发运' }}</span>
        </template>
        <template v-else>
          <span class="chip">发运状态：{{ data.shipment_status }}</span>
          <span class="chip" :class="data.can_confirm ? 'chip-done' : 'chip-warn'">
            {{ data.can_confirm ? '印件核对通过，可确认发运' : `不能确认：${data.block_reason}` }}
          </span>
          <span v-if="data.receipt_locked" class="chip chip-done">回执已锁定真实标签</span>
        </template>
      </section>

      <!-- 文案与更正 -->
      <section v-if="mode === 'packing'" class="recon-block">
        <h5>现行标签文案</h5>
        <p class="label-text">{{ data['现行标签文案'] || '—' }}</p>
        <template v-if="data.correction_history && data.correction_history.length">
          <h5>文案更正记录</h5>
          <ul class="correct-list">
            <li v-for="(c, idx) in data.correction_history" :key="idx" class="correct-item">
              <div><del>{{ c['原文案'] }}</del> → <strong>{{ c['更正文案'] }}</strong></div>
              <div class="muted">{{ c['更正时间'] }} · {{ c['操作人'] }}<template v-if="c['原因']"> · {{ c['原因'] }}</template></div>
              <div class="muted">{{ c['已重印'] ? `已重印换签（${c['重印印件编号'] || ''}）` : '尚未重新印签' }}</div>
            </li>
          </ul>
        </template>
      </section>

      <!-- 发运回执真实标签 -->
      <section v-else class="recon-block">
        <h5>已确认回执保留的真实标签</h5>
        <p class="label-text">{{ data.adopted_label_text || '尚无采纳回执' }}</p>
        <p v-if="data.scanned_imprint" class="muted">
          当前扫码印件：{{ data.scanned_imprint['印件编号'] }}（{{ data.scanned_imprint['状态'] }}）
        </p>
      </section>

      <!-- 全部留印凭据：旧印 / 更正印 / 采纳印 -->
      <section class="recon-block">
        <h5>留印凭据清单（旧印在前，新印在后）</h5>
        <table class="imprint-table">
          <thead>
            <tr>
              <th>印件编号</th><th>印次</th><th>标签文案快照</th><th>留印时间</th>
              <th>确认进口</th><th>状态</th><th>发运号</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="im in imprints" :key="im.id" :data-status="im['状态']">
              <td>{{ im['印件编号'] }}</td>
              <td>第{{ im.sequence }}印</td>
              <td class="snapshot">{{ im['标签文案快照'] }}</td>
              <td class="muted">{{ im['留印时间'] }}</td>
              <td>{{ im['确认进口'] }}</td>
              <td><span class="status-tag" :class="statusClass(im['状态'])">{{ im['状态'] }}</span></td>
              <td>{{ im['物流单号'] || '—' }}</td>
            </tr>
            <tr v-if="!imprints.length"><td colspan="7" class="empty-state">暂无留印凭据</td></tr>
          </tbody>
        </table>
      </section>

      <!-- 更正弹窗 -->
      <div v-if="showCorrect" class="modal-backdrop" @click.self="showCorrect = false">
        <form class="modal small-modal" @submit.prevent="submitCorrect">
          <h5>更正标签文案</h5>
          <p class="muted">更正只更新现行文案并挂起；旧印件留档，需「重印新标签」后仓库重新扫码。</p>
          <textarea v-model="correctText" rows="3" required></textarea>
          <input v-model="correctReason" placeholder="更正原因（可选）" />
          <div class="modal-actions">
            <button class="btn" type="button" @click="showCorrect = false">取消</button>
            <button class="btn primary" type="submit" :disabled="busy">提交更正</button>
          </div>
        </form>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  correctImprint,
  fetchPackingReconciliation,
  fetchShipmentReconciliation,
  issueImprint,
  reprintImprint,
  scanImprint,
} from '@/api/label-service'

const props = defineProps<{ mode: 'packing' | 'shipment'; id: number }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const title = computed(() =>
  props.mode === 'packing' ? `包装批 #${props.id} 标签留印核对` : `发运单 #${props.id} 标签核对`,
)
const loading = ref(true)
const busy = ref(false)
const errorText = ref('')
const infoText = ref('')
const data = ref<Record<string, any>>({})
const showCorrect = ref(false)
const correctText = ref('')
const correctReason = ref('')
const scanNo = ref('')

const imprints = computed<any[]>(() =>
  props.mode === 'packing' ? data.value.imprints || [] : data.value.all_imprints || [],
)

async function load() {
  loading.value = true
  errorText.value = ''
  try {
    data.value =
      props.mode === 'packing'
        ? await fetchPackingReconciliation(props.id)
        : await fetchShipmentReconciliation(props.id)
    if (props.mode === 'shipment' && data.value.scanned_imprint?.['印件编号']) {
      scanNo.value = data.value.scanned_imprint['印件编号']
    }
  } catch (e: any) {
    errorText.value = e?.message || '核对数据读取失败'
  } finally {
    loading.value = false
  }
}

async function callApi(fn: () => Promise<any>, okText: string) {
  busy.value = true
  errorText.value = ''
  infoText.value = ''
  try {
    const res = await fn()
    if (res.ok === false) throw new Error(res.message || '操作失败')
    infoText.value = res.message || okText
    await load()
    emit('changed')
  } catch (e: any) {
    errorText.value = e?.message || '操作失败'
  } finally {
    busy.value = false
  }
}

function onIssue() {
  void callApi(() => issueImprint(props.id), '留印凭据已签发')
}
function onReprint() {
  void callApi(() => reprintImprint(props.id), '重印完成，旧印件已作废')
}
function openCorrect() {
  correctText.value = data.value['现行标签文案'] || ''
  correctReason.value = ''
  showCorrect.value = true
}
function submitCorrect() {
  showCorrect.value = false
  void callApi(
    () => correctImprint(props.id, { new_text: correctText.value, reason: correctReason.value }),
    '文案已更正，等待重印',
  )
}
function onScan() {
  if (!scanNo.value.trim()) {
    errorText.value = '请输入留印凭据编号'
    return
  }
  void callApi(() => scanImprint(props.id, { imprint_no: scanNo.value.trim() }), '扫码绑定成功')
}

function statusClass(status: string) {
  return {
    已签发: 'st-issued',
    已扫码: 'st-scanned',
    已采纳: 'st-consumed',
    已作废: 'st-void',
  }[status] || ''
}

onMounted(load)
defineExpose({ reload: load })
</script>

<style scoped>
.recon-panel {
  margin: 12px 0;
  padding: 14px 16px;
  background: #f7faff;
  border: 1px solid #d6e4ff;
  border-left: 4px solid #2f6fed;
  border-radius: 8px;
}
.recon-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.recon-head h4 { margin: 0; font-size: 15px; color: #1f2d3d; }
.btn.small { padding: 4px 10px; font-size: 12px; }
.recon-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
.scan-form { display: flex; gap: 6px; flex: 1; min-width: 260px; }
.scan-form input { flex: 1; padding: 5px 8px; }
.recon-summary { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.chip {
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 12px;
  background: #eef1f6;
  color: #475569;
}
.chip-done { background: #dcfce7; color: #166534; }
.chip-pending { background: #fef9c3; color: #854d0e; }
.chip-warn { background: #fee2e2; color: #991b1b; }
.recon-block { margin-top: 10px; background: #fff; border: 1px solid #e5eaf2; border-radius: 6px; padding: 8px 10px; }
.recon-block h5 { margin: 4px 0; font-size: 13px; color: #334155; }
.label-text { margin: 4px 0; font-weight: 600; color: #0f172a; }
.muted { color: #64748b; font-size: 12px; }
.correct-list { list-style: none; padding: 0; margin: 6px 0 0; }
.correct-item { padding: 6px 0; border-top: 1px dashed #e2e8f0; }
.imprint-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.imprint-table th, .imprint-table td { border: 1px solid #e2e8f0; padding: 5px 7px; text-align: left; vertical-align: top; }
.imprint-table th { background: #f1f5f9; color: #334155; }
.imprint-table .snapshot { max-width: 260px; }
.status-tag { padding: 2px 8px; border-radius: 10px; font-size: 11px; white-space: nowrap; }
.st-issued { background: #e0e7ff; color: #3730a3; }
.st-scanned { background: #dbeafe; color: #1e40af; }
.st-consumed { background: #dcfce7; color: #166534; }
.st-void { background: #f1f5f9; color: #94a3b8; text-decoration: line-through; }
.empty-state { text-align: center; color: #94a3b8; padding: 12px; }
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.small-modal { width: 460px; max-width: 92vw; display: flex; flex-direction: column; gap: 8px; }
.small-modal textarea, .small-modal input { width: 100%; padding: 7px 9px; border: 1px solid #cbd5e1; border-radius: 5px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.info-text { color: #166534; font-size: 12px; }
</style>
