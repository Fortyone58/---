<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, formatDate, state } from '../api'
import Icon from '../components/Icon.vue'
import JobCard from '../components/JobCard.vue'
import '../agent.css'

const router = useRouter()
const status = ref(null), conversations = ref([]), messages = ref([]), conversationId = ref('')
const question = ref(''), pendingQuestion = ref(''), busy = ref(false), loading = ref(true), error = ref('')
const input = ref(null), thread = ref(null), copied = ref(''), historyOpen = ref(false)
const role = computed(() => state.user?.role)
const maxLength = computed(() => status.value?.max_message_length || 1800)
const connectionLabel = computed(() => {
  if (!status.value) return loading.value ? '正在获取运行状态' : '运行状态暂未取得'
  if (!status.value.enabled || !status.value.configured) return '原文与业务查询'
  return status.value.connection_verified ? '模型接口已验证' : '模型已配置 · 待验证'
})
const isReady = computed(() => status.value?.enabled && status.value?.configured)
const sessionTitle = computed(() => conversations.value.find(item => item.conversation_id === conversationId.value)?.title || '新的校园服务对话')
const roleContent = computed(() => ({
  student: {
    title: '今天有什么可以帮你？', description: '问政策、找岗位、看申请，把课余生活的事情说清楚。',
    examples: [
      { icon: 'book', title: '政策有据可查', text: '武汉设计工程学院勤工助学一周能做几小时？', hint: '查已核验原文，说明学校与国家规定的范围' },
      { icon: 'briefcase', title: '一句话找岗位', text: '我周一下午有空，会 Excel，帮我找适合的岗位', hint: '读取当前岗位，并解释匹配依据' },
      { icon: 'files', title: '了解申请进度', text: '我的申请现在是什么进度，接下来该做什么？', hint: '查询本人申请，给出下一步建议' },
      { icon: 'edit', title: '整理申请理由', text: '帮我整理一份图书馆岗位申请理由，我会 Excel，做事认真。不要编造经历。', hint: '接入模型后生成草稿，由你核对和提交' },
    ],
  },
  unit: {
    title: '把校园用工的事情，说清楚。', description: '查询本单位业务，解读政策，协助准备岗位和工作材料。',
    examples: [
      { icon: 'edit', title: '起草岗位文案', text: '帮我起草一个校园学生助理的招聘文案，需要 Excel 技能，时间和酬金留待确认。', hint: '接入模型后生成草稿，确认后在岗位管理发布' },
      { icon: 'clock', title: '解释工时汇总', text: '查看本单位 2026 年 9 月工时和薪酬估算，解释待核实部分。', hint: '调用现有业务统计，保留待核金额的区别' },
      { icon: 'files', title: '梳理申请进度', text: '本单位还有哪些申请需要处理？', hint: '仅查询当前单位的数据' },
      { icon: 'book', title: '核对政策依据', text: '勤工助学临时岗位的酬金有哪些国家规定？', hint: '展示真实出处，具体校内标准以学校为准' },
    ],
  },
  aid: {
    title: '让校园服务，始终有依据。', description: '核对政策和统计资料，协助理解规则，减少重复咨询。',
    examples: [
      { icon: 'book', title: '政策问答', text: '学生勤工助学有哪些工时限制，寒暑假有什么例外？', hint: '检索已核验的官方材料' },
      { icon: 'shield', title: '辨别适用范围', text: '湖北非全日制最低工资能直接作为学校勤工助学时薪吗？', hint: '区分劳动标准与校内勤工助学制度' },
      { icon: 'clock', title: '解读月度汇总', text: '解释 2026 年 9 月工时和薪酬汇总中的待核实金额。', hint: '读取当前权限范围内的业务汇总' },
      { icon: 'edit', title: '准备咨询回复', text: '帮我写一段对学生的勤工助学咨询回复，学校具体酬金标准未知时请明确说明。', hint: '接入模型后整理回复，原文可逐项核对' },
    ],
  },
  admin: {
    title: '用对话连接知识与校园服务。', description: '配置模型后启用 AI 对话，查询证据和业务，观察实际工具调用。',
    examples: [
      { icon: 'book', title: '检验知识库', text: '武汉设计工程学院勤工助学咨询可以联系哪个部门？', hint: '从已核验校方资料中查找出处' },
      { icon: 'shield', title: '检验回答边界', text: '武汉设计工程学院当前勤工助学的具体时薪是多少？', hint: '缺少现行校内依据时应如实说明' },
      { icon: 'clock', title: '查看业务汇总', text: '解释 2026 年 9 月工时和薪酬汇总。', hint: '调用业务系统已有计算结果' },
      { icon: 'help', title: '了解使用流程', text: '学生从找岗位到上岗登记工时，应该怎样使用这个系统？', hint: '结合角色和页面给出操作路径' },
    ],
  },
}[role.value] || { title: '今天有什么可以帮你？', description: '', examples: [] }))

function readableError(value) {
  if (value.name === 'AbortError') return '等待响应超时。提问记录可能已经保存，请刷新会话查看后再重试。'
  return value.message === 'Failed to fetch' ? '无法连接服务，请检查“启动体验”是否已经运行。' : value.message
}
function rememberConversation(identity) {
  conversationId.value = identity
  sessionStorage.setItem(`qinghe-agent-conversation-${state.user.id}`, identity)
}
async function scrollToEnd() { await nextTick(); if (thread.value) thread.value.scrollTop = thread.value.scrollHeight }
async function newConversation(focus = true) {
  if (busy.value) return
  rememberConversation(`agent-${crypto.randomUUID()}`)
  messages.value = []
  question.value = ''
  error.value = ''
  historyOpen.value = false
  if (focus) { await nextTick(); input.value?.focus() }
}
async function loadConversations() {
  const data = await api('/agent/conversations')
  conversations.value = data.items || []
}
async function refreshStatus() {
  try { status.value = await api('/agent/status') }
  catch (value) { error.value = readableError(value) }
}
async function openConversation(identity) {
  if (busy.value || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const data = await api(`/agent/history?conversation_id=${encodeURIComponent(identity)}`)
    rememberConversation(identity)
    messages.value = data.items || []
    question.value = ''
    historyOpen.value = false
    await scrollToEnd()
  } catch (value) { error.value = readableError(value) }
  finally { loading.value = false }
}
async function send(text = null) {
  if (busy.value || loading.value) return
  if (typeof text === 'string') question.value = text
  const submitted = question.value.trim()
  if (submitted.length < 2) { error.value = '请至少输入两个字，说明想了解的事情。'; input.value?.focus(); return }
  if (submitted.length > maxLength.value) { error.value = `问题请控制在 ${maxLength.value} 字以内。`; return }
  if (!conversationId.value) await newConversation(false)
  busy.value = true
  pendingQuestion.value = submitted
  error.value = ''
  await scrollToEnd()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 85000)
  try {
    const response = await api('/agent/messages', { method: 'POST', body: { question: submitted, conversation_id: conversationId.value }, signal: controller.signal })
    if (response.conversation_id) rememberConversation(response.conversation_id)
    messages.value.push({ id: response.id || `local-${Date.now()}`, question: submitted, response, created_at: response.created_at })
    question.value = ''
    try { await loadConversations() } catch { /* 已收到的回答仍可阅读。 */ }
  } catch (value) { error.value = readableError(value) }
  finally { clearTimeout(timer); busy.value = false; pendingQuestion.value = ''; await scrollToEnd(); input.value?.focus() }
}
function handleEnter(event) {
  if (event.isComposing || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return
  event.preventDefault()
  send()
}
function citationData(hit) {
  return { ...hit, ...(hit.source || {}), section: hit.section || hit.location, text: hit.text, metadata: hit.metadata || hit.source?.source_metadata || hit.source_metadata || {} }
}
function safeSourceUrl(hit) {
  const value = hit.quote_source_url || citationData(hit).source_url
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null } catch { return null }
}
function citationScope(hit) {
  const source = citationData(hit)
  return source.applicability || source.metadata?.applicability || source.metadata?.usage_scope || ''
}
function citationLimitations(hit) {
  const source = citationData(hit)
  const value = source.limitations || source.metadata?.limitations
  return Array.isArray(value) ? value.join('；') : value || ''
}
function jobsFor(response) { return (response.jobs || []).map(item => ({ ...item, job: item.job || item, score: item.score ?? item.matching?.total ?? null })) }
function modeLabel(response) { return response.mode === 'model' ? 'AI 回答' : '原文与业务查询' }
async function copyAnswer(entry) {
  try {
    await navigator.clipboard.writeText(entry.response.answer || '')
    copied.value = String(entry.id)
    setTimeout(() => { if (copied.value === String(entry.id)) copied.value = '' }, 2200)
  } catch { error.value = '浏览器未能复制内容，请选中回答文字后手动复制。' }
}
onMounted(async () => {
  const results = await Promise.allSettled([refreshStatus(), loadConversations()])
  for (const result of results) if (result.status === 'rejected') error.value = readableError(result.reason)
  loading.value = false
  const remembered = sessionStorage.getItem(`qinghe-agent-conversation-${state.user.id}`)
  if (remembered && conversations.value.some(item => item.conversation_id === remembered)) await openConversation(remembered)
  else { const initialError = error.value; await newConversation(false); error.value = initialError }
})
</script>

<template>
  <div class="agent-page">
    <div class="page-heading agent-page-heading"><div><p class="section-kicker">ASK, CONNECT, GET THINGS CLEAR</p><h1>青禾 AI 服务助手<span class="heading-dot">.</span></h1><p>从一句话开始，连接政策原文、岗位与校园服务。</p></div><router-link v-if="role === 'admin'" to="/ai-settings" class="btn btn-secondary"><Icon name="settings" :size="17" />模型配置</router-link></div>
    <div class="agent-layout">
      <aside class="agent-history panel" :class="{ 'agent-history-open': historyOpen }">
        <button class="btn btn-secondary agent-new" :disabled="busy || loading" @click="newConversation()"><Icon name="plus" :size="17" />新对话</button>
        <div class="agent-history-heading"><span>我的对话</span><button class="icon-button agent-history-close" aria-label="收起对话历史" @click="historyOpen = false"><Icon name="x" :size="17" /></button></div>
        <nav class="agent-history-list" aria-label="我的 AI 服务对话"><button v-for="item in conversations" :key="item.conversation_id" class="agent-history-item" :class="{ selected: item.conversation_id === conversationId }" :disabled="busy || loading" :aria-current="item.conversation_id === conversationId ? 'true' : undefined" @click="openConversation(item.conversation_id)"><Icon name="files" :size="16" /><span><b>{{ item.title }}</b><small>{{ item.message_count }} 次提问 · {{ formatDate(item.updated_at) }}</small></span></button><p v-if="!conversations.length" class="agent-history-empty">从一个问题开始，<br>对话会保存在这里。</p></nav>
        <div class="agent-history-foot"><Icon name="shield" :size="17" /><p>历史仅本人可见。<br>业务查询遵循当前账号权限。</p></div>
      </aside>
      <section class="agent-chat panel">
        <header class="agent-chat-header"><div><button class="icon-button agent-history-toggle" aria-label="查看我的对话历史" :disabled="busy" @click="historyOpen = !historyOpen"><Icon name="history" :size="19" /></button><span class="agent-bot-mark"><Icon name="sparkles" :size="21" /></span><div><b>{{ sessionTitle }}</b><small><span class="live-dot" :class="{ 'agent-dot-muted': !isReady }"></span>{{ connectionLabel }}</small></div></div><button class="icon-button" aria-label="刷新 AI 配置状态" :disabled="busy" @click="refreshStatus"><Icon name="refresh" :size="17" /></button></header>
        <div v-if="status && !isReady" class="agent-mode-banner"><Icon name="info" :size="17" /><span>当前使用原文与业务查询。<template v-if="role === 'admin'">接入模型后可进行 AI 多轮对话和文案起草。<router-link to="/ai-settings">去配置模型<Icon name="right" :size="14" /></router-link></template><template v-else>管理员接入模型后，即可启用 AI 对话和文案起草。</template></span></div>
        <div v-else-if="status && !status.connection_verified" class="agent-mode-banner"><Icon name="info" :size="17" /><span>模型接口已配置，连接尚未验证。每条回答会注明实际使用模式。</span></div>
        <div ref="thread" class="agent-thread" :aria-busy="busy || loading">
          <el-skeleton v-if="loading" :rows="5" animated />
          <section v-else-if="!messages.length && !busy" class="agent-welcome"><span class="agent-welcome-symbol"><Icon name="sparkles" :size="32" /></span><span class="section-kicker">YOUR CAMPUS COMPANION</span><h2>{{ roleContent.title }}</h2><p>{{ roleContent.description }}</p><div class="agent-example-grid"><button v-for="example in roleContent.examples" :key="example.title" class="agent-example" :disabled="busy" @click="send(example.text)"><span><Icon :name="example.icon" :size="18" /><b>{{ example.title }}</b><Icon name="arrow" :size="14" /></span><p>{{ example.text }}</p><small>{{ example.hint }}</small></button></div><p class="agent-welcome-tip"><Icon name="history" :size="15" />同一对话中可以追问，也可点击左侧开始新对话。</p></section>
          <div v-for="entry in messages" :key="entry.id" class="agent-turn">
            <div class="agent-user-message"><div>{{ entry.question }}</div><span class="agent-user-avatar"><Icon name="user" :size="17" /></span></div>
            <article class="agent-reply"><span class="agent-reply-avatar"><Icon name="sparkles" :size="18" /></span><div class="agent-reply-content"><div class="agent-answer-meta"><b>青禾</b><span :class="{ 'is-ai': entry.response.mode === 'model' }">{{ modeLabel(entry.response) }}</span><small v-if="entry.response.mode === 'model' && entry.response.model">{{ entry.response.model }}</small></div><p class="agent-answer-text">{{ entry.response.answer }}</p>
              <div v-if="entry.response.tool_steps?.length" class="agent-tools"><details><summary><Icon name="check" :size="15" /><span>实际查询与执行记录 · {{ entry.response.tool_steps.length }} 项</span><Icon name="chevron" :size="14" /></summary><ol><li v-for="(step, index) in entry.response.tool_steps" :key="`${step.name}-${index}`"><Icon :name="step.status === 'rejected' ? 'shield' : 'check'" :size="15" /><div><b>{{ step.label || step.name }}<small v-if="step.duration_ms !== undefined">{{ step.duration_ms }} ms</small></b><p>{{ step.summary }}</p></div><span v-if="step.status === 'rejected'" class="agent-tool-rejected">已拒绝</span></li></ol><p class="agent-tools-note">展示实际工具调用结果，不包含模型内部思考。</p></details></div>
              <div v-if="entry.response.citations?.length" class="agent-sources"><details><summary><Icon name="book" :size="15" /><span>查看原文依据 · {{ entry.response.citations.length }} 段</span><Icon name="chevron" :size="14" /></summary><article v-for="(hit, index) in entry.response.citations" :key="`${citationData(hit).source_id || citationData(hit).id}-${index}`" class="agent-source"><div><span class="agent-source-number">{{ index + 1 }}</span><b>{{ citationData(hit).title }}</b><span v-if="citationData(hit).source_key" class="mini-label">{{ citationData(hit).source_key }}</span></div><p class="agent-source-location">{{ citationData(hit).publisher }}<template v-if="citationData(hit).version"> · {{ citationData(hit).version }}</template><template v-if="citationData(hit).section"> · {{ citationData(hit).section }}</template></p><div v-if="citationData(hit).past_deadline || citationData(hit).usage_scope === 'archive_only'" class="agent-source-archive"><Icon name="history" :size="13" />历史资料，仅作对应年度参考<template v-if="citationData(hit).expires_at"> · 截止 {{ formatDate(citationData(hit).expires_at) }}</template></div><blockquote>{{ citationData(hit).text }}</blockquote><p v-if="citationScope(hit)" class="agent-source-scope">适用范围：{{ citationScope(hit) }}</p><p v-if="citationLimitations(hit)" class="agent-source-scope">使用限制：{{ citationLimitations(hit) }}</p><a v-if="safeSourceUrl(hit)" :href="safeSourceUrl(hit)" target="_blank" rel="noopener noreferrer" class="text-button">打开引用原文<Icon name="external" :size="14" /></a></article></details></div>
              <div v-if="entry.response.jobs?.length" class="agent-job-grid"><JobCard v-for="item in jobsFor(entry.response)" :key="item.job.id" :job="item.job" :score="item.score" @view="job => router.push(`/jobs?job=${job.id}`)" /></div>
              <p v-if="entry.response.notice" class="agent-reply-notice"><Icon name="info" :size="14" />{{ entry.response.notice }}</p><div class="agent-reply-actions"><button class="text-button" @click="copyAnswer(entry)"><Icon :name="copied === String(entry.id) ? 'check' : 'files'" :size="14" />{{ copied === String(entry.id) ? '已复制' : '复制回答' }}</button><span v-if="entry.created_at">{{ formatDate(entry.created_at) }}</span></div>
            </div></article>
          </div>
          <div v-if="busy" class="agent-turn"><div class="agent-user-message"><div>{{ pendingQuestion }}</div><span class="agent-user-avatar"><Icon name="user" :size="17" /></span></div><div class="agent-reply"><span class="agent-reply-avatar"><Icon name="sparkles" :size="18" /></span><div class="agent-waiting" role="status"><div><span></span><span></span><span></span></div><b>{{ isReady ? '正在处理你的问题…' : '正在查询原文与业务…' }}</b><p>{{ isReady ? '理解问题 → 按需查知识 / 读业务 → 组织回答' : '检索已核验原文，查询当前权限内的业务数据' }}</p><small>回答完成后显示实际调用的工具与依据。</small></div></div></div>
        </div>
        <div v-if="error" class="agent-error" role="alert"><Icon name="alert" :size="17" /><span>{{ error }}</span><button class="icon-button" aria-label="关闭错误提示" @click="error = ''"><Icon name="x" :size="15" /></button></div>
        <form class="agent-composer" @submit.prevent="send()"><label class="agent-input-label" for="agent-message">向青禾提问</label><div class="agent-input-wrap"><textarea id="agent-message" ref="input" v-model="question" rows="2" :maxlength="maxLength" :disabled="busy || loading" :placeholder="messages.length ? '继续追问，或告诉我新的条件…' : '问政策、找岗位、看申请，或者描述你需要的帮助…'" required minlength="2" @keydown.enter="handleEnter"></textarea><button class="agent-send" :disabled="busy || loading || question.trim().length < 2" :aria-label="busy ? '正在等待回答' : '发送问题'"><Icon :name="busy ? 'hourglass' : 'send'" :size="19" /></button></div><div class="agent-composer-foot"><span>Enter 发送 · Shift + Enter 换行</span><span>{{ question.length }} / {{ maxLength }}</span></div><p class="agent-boundary-note">回答和草稿请核对原文；申请、审批和工时变更由你在对应页面确认。请勿输入密钥或身份证等敏感信息。</p></form>
      </section>
    </div>
  </div>
</template>
