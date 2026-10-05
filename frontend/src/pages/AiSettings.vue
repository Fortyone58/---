<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, formatDate, state } from '../api'
import Icon from '../components/Icon.vue'
import '../agent.css'

const saved = ref(null), loading = ref(true), busy = ref(false), testing = ref(false), error = ref(''), success = ref(''), testResult = ref(null)
const form = reactive({ enabled: false, provider: 'deepseek', base_url: '', model: '', api_key: '', clear_api_key: false })
const knowledge = ref(null), rebuilding = ref(false), knowledgeError = ref('')
const knowledgeLabel = computed(() => ({ ready: '语义索引可用', stale: '原文已更新', disabled: '语义检索已关闭', not_ready: '尚未建立语义索引', unavailable: '语义检索暂不可用' }[knowledge.value?.state] || '正在读取知识库'))
const fallbackPresets = [
  { id: 'deepseek', label: 'DeepSeek' }, { id: 'mimo', label: '小米 MiMo' },
  { id: 'bailian', label: '阿里云百炼' }, { id: 'custom', label: '其他 OpenAI 兼容接口' }, { id: 'ollama', label: '本机 Ollama' },
]
const presets = computed(() => saved.value?.presets?.length ? saved.value.presets : fallbackPresets)
const selectedPreset = computed(() => presets.value.find(item => item.id === form.provider))
const changed = computed(() => saved.value && (form.enabled !== saved.value.enabled || form.provider !== saved.value.provider || form.base_url.trim() !== saved.value.base_url || form.model.trim() !== saved.value.model || !!form.api_key || form.clear_api_key))
const changingHost = computed(() => {
  if (!saved.value?.base_url || !form.base_url) return false
  try { return new URL(saved.value.base_url).origin !== new URL(form.base_url).origin } catch { return true }
})
function readableError(value) { return value.message === 'Failed to fetch' ? '无法连接服务，请检查“启动体验”是否已经运行。' : value.message }
function applySettings(data) {
  saved.value = data
  Object.assign(form, { enabled: data.enabled, provider: data.provider, base_url: data.base_url || '', model: data.model || '', api_key: '', clear_api_key: false })
}
function chooseProvider() {
  const preset = selectedPreset.value
  form.api_key = ''
  form.clear_api_key = false
  if (preset?.base_url) form.base_url = preset.base_url
  else form.base_url = ''
  form.model = preset?.model || ''
  testResult.value = null
  success.value = ''
}
async function save() {
  if (busy.value || testing.value) return
  busy.value = true
  error.value = ''
  success.value = ''
  testResult.value = null
  try {
    const body = { enabled: form.enabled, provider: form.provider, base_url: form.base_url.trim(), model: form.model.trim(), clear_api_key: form.clear_api_key }
    if (form.api_key.trim()) body.api_key = form.api_key.trim()
    const data = await api('/admin/ai/settings', { method: 'PUT', body })
    applySettings(data)
    success.value = '模型配置已保存。请测试连接，再到 AI 服务助手体验对话。'
    state.meta = await api('/meta')
  } catch (value) { error.value = readableError(value) }
  finally { form.api_key = ''; busy.value = false }
}
async function testConnection() {
  if (busy.value || testing.value) return
  if (changed.value) { error.value = '请先保存当前修改，再测试已保存的模型配置。'; return }
  testing.value = true
  error.value = ''
  success.value = ''
  testResult.value = null
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 45000)
  try {
    testResult.value = await api('/admin/ai/test', { method: 'POST', body: {}, signal: controller.signal })
    saved.value.connection_verified = testResult.value.connection_verified
    state.meta = await api('/meta')
  } catch (value) { error.value = value.name === 'AbortError' ? '连接测试等待超时，请确认服务地址、模型和密钥后重试。' : readableError(value) }
  finally { clearTimeout(timer); testing.value = false }
}
function safeDocsUrl(value) { try { const url = new URL(value); return url.protocol === 'https:' ? url.href : null } catch { return null } }
async function refreshKnowledge() {
  knowledgeError.value = ''
  try { knowledge.value = await api('/admin/rag/status') }
  catch (value) { knowledgeError.value = readableError(value) }
}
async function rebuildKnowledge() {
  if (rebuilding.value) return
  rebuilding.value = true
  knowledgeError.value = ''
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 120000)
  try { knowledge.value = await api('/admin/rag/rebuild', { method: 'POST', body: {}, signal: controller.signal }) }
  catch (value) { knowledgeError.value = value.name === 'AbortError' ? '索引重建仍可能在处理，请刷新状态后核对。' : readableError(value) }
  finally { clearTimeout(timer); rebuilding.value = false }
}
onMounted(async () => {
  await refreshKnowledge()
  try { applySettings(await api('/admin/ai/settings')) }
  catch (value) { error.value = readableError(value) }
  finally { loading.value = false }
})
</script>

<template>
  <div class="ai-settings-page">
    <div class="page-heading"><div><p class="section-kicker">ONE ASSISTANT, YOUR CHOICE OF MODEL</p><h1>为青禾接入模型<span class="heading-dot">.</span></h1><p>先接入 DeepSeek，之后可切换 MiMo、百炼或其他兼容接口。</p></div><router-link to="/chat" class="btn btn-secondary"><Icon name="sparkles" :size="17" />打开 AI 服务助手</router-link></div>
    <el-skeleton v-if="loading" :rows="8" animated />
    <div v-else class="ai-settings-layout">
      <form class="panel ai-config-form" @submit.prevent="save">
        <div class="panel-heading"><h3>模型连接</h3><span class="outline-pill"><span class="live-dot" :class="{ 'agent-dot-muted': !saved?.connection_verified }"></span>{{ saved?.connection_verified ? '连接已验证' : '连接尚未验证' }}</span></div>
        <label class="ai-enable"><span><b>启用 AI 对话</b><small>模型理解问题，按需调用只读工具并组织回答。</small></span><input v-model="form.enabled" type="checkbox" :disabled="busy || testing"></label>
        <div class="form-grid"><label class="field">服务商<select v-model="form.provider" :disabled="busy || testing" @change="chooseProvider"><option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.label }}</option></select></label><label class="field">模型 ID<input v-model="form.model" placeholder="填写服务商支持的模型 ID" maxlength="120" :required="form.enabled" :disabled="busy || testing"></label></div>
        <label class="field">API 基础地址<input v-model="form.base_url" type="url" placeholder="https://服务商地址/v1" maxlength="500" :required="form.enabled" :disabled="busy || testing"><small>填写基础地址，无需添加 /chat/completions。远程接口使用 HTTPS，本机 Ollama 可使用 HTTP。</small></label>
        <label class="field">API 密钥<input v-model="form.api_key" type="password" autocomplete="new-password" spellcheck="false" :placeholder="saved?.has_api_key && !changingHost ? '本机已保存密钥；留空保留' : selectedPreset?.requires_key === false ? '本机服务通常无需密钥' : '在本机填写服务商 API 密钥'" maxlength="500" :disabled="busy || testing || form.clear_api_key"><small>密钥仅在提交时发送到本机后端，不会回显，也不会保存到浏览器。</small></label>
        <label v-if="saved?.has_api_key" class="checkbox-label ai-clear-key"><input v-model="form.clear_api_key" type="checkbox" :disabled="busy || testing" @change="form.api_key = ''">清除本机保存的密钥</label>
        <p v-if="changingHost && saved?.has_api_key" class="notice"><Icon name="shield" :size="18" /><span>你已切换服务地址，请填写新服务的密钥，或明确清除旧密钥后使用无密钥服务。</span></p>
        <a v-if="safeDocsUrl(selectedPreset?.docs_url)" :href="safeDocsUrl(selectedPreset.docs_url)" target="_blank" rel="noopener noreferrer" class="text-button ai-doc-link">查看{{ selectedPreset.label }}官方接入文档<Icon name="external" :size="14" /></a>
        <div v-if="error" class="agent-error" role="alert"><Icon name="alert" :size="17" /><span>{{ error }}</span></div><div v-if="success" class="ai-success" role="status"><Icon name="check" :size="17" /><span>{{ success }}</span></div><div v-if="testResult" class="ai-test-result" :class="{ 'ai-test-failed': !testResult.ok }" role="status"><Icon :name="testResult.ok ? 'check' : 'alert'" :size="18" /><span><b>{{ testResult.ok ? '连接测试通过' : '连接测试未通过' }}</b>{{ testResult.message }}</span></div>
        <div class="ai-config-actions"><button class="btn btn-primary" :disabled="busy || testing">{{ busy ? '保存中…' : '保存配置' }}<Icon name="check" :size="16" /></button><button type="button" class="btn btn-secondary" :disabled="busy || testing || !saved || changed || !saved.enabled" @click="testConnection">{{ testing ? '测试连接中…' : '测试已保存的连接' }}<Icon :name="testing ? 'hourglass' : 'refresh'" :size="16" /></button></div><p class="caption ai-test-note">测试只发送固定问候，不使用学生档案或业务数据。切换服务商后，先保存再测试。</p>
      </form>
      <aside class="ai-config-aside"><section class="panel"><span class="agent-bot-mark"><Icon name="sparkles" :size="25" /></span><h3>三步开始 AI 对话</h3><ol class="ai-setup-steps"><li><span>01</span><div><b>选择服务商与模型</b><p>从官方控制台确认模型 ID 和接口地址，模型名称可以随时修改。</p></div></li><li><span>02</span><div><b>填写密钥，保存并测试</b><p>密钥留空只保留同一服务已有密钥。连接测试成功后再开始体验。</p></div></li><li><span>03</span><div><b>打开助手，试着提问</b><p>先查政策，再用自然语言找岗位；同一对话中可继续追问。</p></div></li></ol><router-link to="/chat" class="text-button">开始一段校园服务对话<Icon name="right" :size="15" /></router-link></section><section class="panel ai-config-boundary"><h3>模型与业务工具各司其职</h3><p>模型负责理解问题、选择工具、解释结果和起草文案。政策原文、岗位状态和薪酬统计来自已收录资料与业务系统。</p><p>AI 工具读取当前账号权限范围的数据；申请、审批、困难认定和工时修改由人在对应页面完成。</p><div class="notice neutral"><Icon name="info" :size="17" /><span>远程模型会收到当前问题及回答所需的少量模拟业务数据。此原型使用演示数据，请勿提交真实学生的个人信息。</span></div><div class="notice neutral"><Icon name="info" :size="17" /><span>关闭模型或接口不可用时，助手会清楚标注“原文与业务查询”。是否调用模型，以每条回答的模式标记为准。</span></div></section></aside>
    </div>
    <section class="rag-index-section" aria-labelledby="rag-heading">
      <div class="panel-heading"><div><h3 id="rag-heading">政策知识库</h3><span class="caption">{{ knowledge?.model || '本地语义模型' }}</span></div><span class="outline-pill"><span class="live-dot" :class="{ 'agent-dot-muted': !knowledge?.ready }"></span>{{ knowledgeLabel }}</span></div>
      <dl class="rag-index-stats"><div><dt>已索引资料</dt><dd>{{ knowledge?.documents ?? '—' }}<small>份</small></dd></div><div><dt>检索片段</dt><dd>{{ knowledge?.chunks ?? '—' }}<small>段</small></dd></div><div><dt>最近更新</dt><dd class="rag-index-date">{{ formatDate(knowledge?.built_at) }}</dd></div></dl>
      <div class="rag-index-actions"><button class="btn btn-secondary" :disabled="rebuilding" @click="refreshKnowledge"><Icon name="refresh" :size="17" />刷新状态</button><button class="btn btn-primary" :disabled="rebuilding || !knowledge?.enabled" @click="rebuildKnowledge"><Icon :name="rebuilding ? 'hourglass' : 'book'" :size="17" />{{ rebuilding ? '正在重建…' : '重建索引' }}</button></div>
      <p v-if="knowledgeError" class="rag-index-error" role="alert">{{ knowledgeError }}</p>
    </section>
  </div>
</template>
