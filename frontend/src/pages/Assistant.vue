<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, formatDate, state } from '../api'
import Icon from '../components/Icon.vue'
import JobCard from '../components/JobCard.vue'
import '../assistant.css'

const router = useRouter()
const draft = ref(''), result = ref(null), history = ref([]), busy = ref(false), loading = ref(false), error = ref('')
const conversationId = 'my-job-assistant'
const examples = ['图书馆 A区临时岗，时薪至少20元', '周一下午，会Excel的岗位', 'C区摄影或设计，时薪至少25元', '不限技能的临时岗']
const reversedHistory = computed(() => [...history.value].reverse())
const resultIcon = computed(() => result.value?.status === 'refused' ? 'shield' : result.value?.items?.length ? 'check' : 'info')
const displayedFilters = computed(() => result.value?.filters?.length ? result.value.filters : result.value?.parsed_filters || [])
const errorText = value => value.message === 'Failed to fetch' ? '无法连接服务，请检查“启动体验”是否已运行。' : value.message

async function loadHistory() {
  loading.value = true
  try {
    const data = await api(`/assistant/history?conversation_id=${encodeURIComponent(conversationId)}`)
    history.value = data.items
    if (!result.value && data.items.length) result.value = data.items.at(-1).response
  } catch (value) { error.value = errorText(value) }
  finally { loading.value = false }
}

async function send(text = null) {
  if (busy.value) return
  if (typeof text === 'string') draft.value = text
  const question = draft.value.trim()
  if (question.length < 2) { error.value = '请至少输入两个字，说明想找的岗位。'; return }
  busy.value = true
  error.value = ''
  try {
    result.value = await api('/assistant/messages', { method: 'POST', body: { question, conversation_id: conversationId } })
    history.value.push({ id: `local-${Date.now()}`, question, response: result.value, created_at: result.value.created_at })
    history.value = history.value.slice(-20)
  } catch (value) { error.value = errorText(value) }
  finally { busy.value = false }
}

function select(entry) {
  draft.value = entry.question
  result.value = entry.response
  error.value = ''
}

function viewJob(job) { router.push(`/jobs?job=${job.id}`) }
onMounted(() => { if (state.user?.role === 'student') loadHistory() })
</script>

<template>
  <div class="assistant-page">
    <div class="page-heading">
      <div><p class="section-kicker">YOUR CONDITIONS, REAL OPPORTUNITIES</p><h1>说说条件，找到合适的岗位<span class="heading-dot">.</span></h1><p>一句话筛选在招岗位，逐项说明哪些条件生效。</p></div>
      <span class="outline-pill"><Icon name="sparkles" :size="16" />条件检索模式</span>
    </div>
    <div v-if="state.user?.role !== 'student'" class="notice"><Icon name="shield" /><span>岗位助手仅供学生使用，请切换到学生账号。</span></div>
    <div v-else class="assistant-layout">
      <section class="assistant-main">
        <div class="panel assistant-compose">
          <div class="assistant-intro"><span class="assistant-symbol"><Icon name="sparkles" :size="25" /></span><div><h2>你想找什么样的岗位？</h2><p>可说单位、区域、技能、固定或临时岗、工资下限、周几时段。</p></div></div>
          <form @submit.prevent="send()">
            <label class="assistant-field" for="assistant-question">岗位需求</label>
            <textarea id="assistant-question" v-model="draft" rows="3" maxlength="500" required minlength="2" placeholder="例如：图书馆 A区临时岗，时薪至少20元，周二下午" :disabled="busy" @keydown.ctrl.enter.prevent="send()"></textarea>
            <div class="assistant-compose-actions"><span class="caption">每次按本次条件检索 · {{ draft.length }} / 500</span><button class="btn btn-primary" :disabled="busy">{{ busy ? '查找岗位中…' : '查找岗位' }}<Icon :name="busy ? 'hourglass' : 'send'" :size="17" /></button></div>
          </form>
          <div class="assistant-examples"><span>试试这样说</span><button v-for="example in examples" :key="example" :disabled="busy" @click="send(example)">{{ example }}<Icon name="arrow" :size="13" /></button></div>
          <p class="assistant-mode-note"><Icon name="info" :size="15" />根据数据库和明确规则检索，未调用大模型。档案不会因提问而修改。</p>
        </div>

        <div v-if="error" class="assistant-error" role="alert"><Icon name="alert" :size="18" /><span>{{ error }}</span><button class="icon-button" aria-label="关闭错误提示" @click="error = ''"><Icon name="x" :size="16" /></button></div>
        <el-skeleton v-if="busy" class="panel assistant-loading" :rows="6" animated />
        <section v-else-if="result" class="assistant-results" aria-live="polite">
          <div class="panel assistant-answer">
            <div class="assistant-question"><Icon name="user" :size="17" /><span>{{ result.question }}</span></div>
            <div class="assistant-answer-text"><Icon :name="resultIcon" :size="23" /><h3>{{ result.answer }}</h3></div>
            <p v-if="result.historical" class="caption assistant-history-note"><Icon name="history" :size="14" />{{ result.history_notice }}</p>
            <div v-if="displayedFilters.length" class="assistant-filters"><span class="assistant-field">{{ result.status === 'needs_clarification' ? '已识别条件（待明确，尚未检索）' : '已生效条件' }}</span><div><span v-for="filter in displayedFilters" :key="filter.key"><Icon name="filter" :size="13" />{{ filter.label }}</span></div></div>
            <div v-if="result.warnings?.length" class="assistant-warnings"><b><Icon name="info" :size="16" />理解说明</b><ul><li v-for="warning in result.warnings" :key="warning">{{ warning }}</li></ul></div>
            <div v-if="result.missing_profile?.length && result.items?.length" class="notice neutral"><Icon name="user" /><span>{{ result.missing_profile.join('；') }}。<router-link to="/profile" class="text-button">完善我的档案<Icon name="right" :size="14" /></router-link></span></div>
            <router-link v-if="result.action" :to="result.action.path" class="btn btn-secondary">{{ result.action.label }}<Icon name="right" :size="16" /></router-link>
            <div v-if="!result.items?.length && !result.action" class="assistant-retry"><button class="text-button" :disabled="busy" @click="send('推荐适合我的岗位')">查看当前推荐岗位<Icon name="right" :size="16" /></button><router-link to="/jobs" class="text-button">浏览岗位列表<Icon name="right" :size="16" /></router-link></div>
          </div>
          <template v-if="result.items?.length">
            <div class="list-caption"><span>当前可申请 {{ result.total }} 个 · 展示 {{ result.shown }} 个</span><button class="text-button" :disabled="busy" @click="send(result.question)"><Icon name="refresh" :size="14" />重新查询</button></div>
            <div class="assistant-jobs"><article v-for="item in result.items" :key="item.job.id" class="assistant-job"><JobCard :job="item.job" :score="item.matching?.total ?? null" @view="viewJob" /><div class="assistant-job-reasons"><span class="assistant-field">符合条件与匹配依据</span><ul><li v-for="reason in item.reasons" :key="reason"><Icon name="check" :size="13" /><span>{{ reason }}</span></li></ul><button class="text-button" @click="viewJob(item.job)">查看详情并自行申请<Icon name="right" :size="15" /></button></div></article></div>
            <p class="caption assistant-result-notice">{{ result.notice }} 招聘名额会变化，申请时由服务端再次校验。</p>
          </template>
        </section>
        <div v-else class="panel assistant-empty"><Icon name="briefcase" :size="32" /><h3>从一个具体条件开始</h3><p>结果只包含当前在招且本人可申请的岗位。<br>助手提供岗位信息和匹配依据，申请由你在详情中确认。</p></div>
      </section>

      <aside class="assistant-aside">
        <section class="panel assistant-history"><div class="panel-heading"><h3>我的提问</h3><button class="icon-button" :disabled="loading || busy" aria-label="刷新我的提问历史" @click="loadHistory"><Icon name="refresh" :size="17" /></button></div><p class="caption">仅显示本人最近 20 次提问。重新进入页面时，历史结果会按当前岗位刷新。</p><el-skeleton v-if="loading" :rows="3" animated /><template v-else><button v-for="entry in reversedHistory" :key="entry.id" class="assistant-history-item" :class="{ active: result?.question === entry.question }" @click="select(entry)"><Icon name="history" :size="15" /><span><b>{{ entry.question }}</b><small>{{ formatDate(entry.created_at) }}</small></span><Icon name="chevron" :size="14" /></button><p v-if="!history.length" class="assistant-history-empty">你还没有提问，试试左侧的示例。</p></template></section>
        <section class="panel assistant-tips"><span class="section-kicker">MAKE THE CONDITIONS CLEAR</span><h3>怎样说更明确？</h3><ul><li>工资注明单位：“时薪至少 20 元”或“固定岗月薪至少 800 元”。</li><li>技能按岗位要求筛选，可说“摄影或设计”“不会 Python”“不限技能”。</li><li>时间一次给一个范围：“周一或周二下午”或“周一 14:00–16:00”。</li><li>完整可用时间请填入档案，再用规则匹配核对全部排班。</li></ul><router-link to="/matching" class="text-button">查看四维规则匹配<Icon name="right" :size="15" /></router-link></section>
      </aside>
    </div>
  </div>
</template>
