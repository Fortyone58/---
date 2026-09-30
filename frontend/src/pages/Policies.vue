<script setup>
import { nextTick, onMounted, reactive, ref } from 'vue'
import { api, perform, state, today, formatDate } from '../api'
import Icon from '../components/Icon.vue'
import '../policy.css'

const docs = ref([]), question = ref(''), messages = ref([]), conversations = ref([])
const conversationId = ref(''), busy = ref(false), loading = ref(false), queryInput = ref(null)
const importing = ref(false), saving = ref(false), docDetail = ref(null), docDrawer = ref(false)
const form = reactive({ title: '', publisher: '', source_url: '', version: '', verified_at: `${today()}T00:00`, verification_note: '', is_school_policy: false, text: '' })

function rememberConversation(identity) {
  conversationId.value = identity
  sessionStorage.setItem(`qinghe-policy-conversation-${state.user.id}`, identity)
}
async function newConversation(focus = true) {
  rememberConversation(`policy-${crypto.randomUUID()}`)
  messages.value = []
  question.value = ''
  if (focus) { await nextTick(); queryInput.value?.focus() }
}
async function loadDocs() { const data = await perform(() => api('/policies')); if (data) docs.value = data.items }
async function loadConversations() { const data = await perform(() => api('/qa/conversations')); if (data) conversations.value = data.items }
async function openConversation(identity) {
  if (busy.value || loading.value) return
  loading.value = true
  const data = await perform(() => api(`/qa/history?conversation_id=${encodeURIComponent(identity)}`))
  if (data) { rememberConversation(identity); messages.value = data.items; question.value = '' }
  loading.value = false
}
onMounted(async () => {
  loading.value = true
  await Promise.all([loadDocs(), loadConversations()])
  loading.value = false
  const remembered = sessionStorage.getItem(`qinghe-policy-conversation-${state.user.id}`)
  if (remembered && conversations.value.some(item => item.conversation_id === remembered)) await openConversation(remembered)
  else await newConversation(false)
})
async function search(text = null) {
  if (busy.value || loading.value) return
  if (typeof text === 'string') question.value = text
  const submitted = question.value.trim()
  if (submitted.length < 2) return
  if (!conversationId.value) await newConversation(false)
  busy.value = true
  const result = await perform(() => api('/qa', { method: 'POST', body: { question: submitted, conversation_id: conversationId.value } }))
  if (result) {
    messages.value.push({ id: `local-${Date.now()}`, question: submitted, response: result, created_at: null })
    question.value = ''
    await loadConversations()
  }
  busy.value = false
}
async function read(doc) { docDetail.value = await perform(() => api(`/policies/${doc.id}`)); if (docDetail.value) docDrawer.value = true }
async function save() {
  saving.value = true
  const sections = form.text.split('\n').map(s => s.trim()).filter(Boolean).map((line, i) => {
    const pos = line.indexOf('|'); return pos > 0 ? { location: line.slice(0, pos).trim(), text: line.slice(pos + 1).trim() } : { location: `原文片段 ${i + 1}`, text: line }
  })
  const { text: _text, ...metadata } = form
  if (await perform(() => api('/policies', { method: 'POST', body: { ...metadata, sections } }), '已核验政策原文已导入')) { importing.value = false; await loadDocs() }
  saving.value = false
}
</script>

<template>
  <div class="policy-page">
    <div class="page-heading">
      <div><p class="section-kicker">ANSWERS BEGIN WITH SOURCES</p><h1>读原文，让了解有依据<span class="heading-dot">.</span></h1><p>用自己的话提问，查看依据；同一会话可以继续追问。</p></div>
      <div class="policy-heading-actions">
        <button class="btn btn-secondary" :disabled="busy || loading" @click="newConversation()"><Icon name="plus" :size="18" />新会话</button>
        <button v-if="state.user.role === 'aid'" class="btn btn-primary" @click="importing = true"><Icon name="book" :size="18" />导入已核验原文</button>
        <span v-else class="outline-pill"><Icon name="book" :size="16" />政策原文查询</span>
      </div>
    </div>
    <div class="policy-layout">
      <section class="panel policy-main">
        <div class="policy-intro"><span class="policy-symbol"><Icon name="book" :size="28" /></span><h2>关于勤工助学，<br>从一个问题开始。</h2><p>例如“一周最多工作几小时”，或指定“第 21 条”。每个片段都保留实际出处。</p></div>
        <form class="policy-search" @submit.prevent="search()"><Icon name="search" :size="21" /><input ref="queryInput" v-model.trim="question" aria-label="政策问题" :placeholder="messages.length ? '继续提问，例如：那寒暑假呢？' : '一周最多工作几小时？'" required minlength="2" maxlength="500" :disabled="busy || loading"><button class="btn btn-primary" :disabled="busy || loading">{{ busy ? '查询中…' : '查找依据' }}<Icon name="right" :size="17" /></button></form>
        <div class="query-chips"><span>试着查查</span><button v-for="q in ['每周工时上限','寒暑假时间限制','固定岗位工资','临时岗位酬金']" :key="q" :disabled="busy || loading" @click="search(q)">{{ q }}<Icon name="arrow" :size="13" /></button></div>
        <div v-if="loading" class="policy-placeholder" role="status"><Icon name="hourglass" /><span>正在载入这个会话的政策记录…</span></div>
        <div v-else-if="messages.length" class="policy-conversation" aria-live="polite">
          <div class="policy-conversation-label"><Icon name="history" :size="16" /><span>当前会话 · {{ messages.length }} 次提问</span><small>点击问题展开依据</small></div>
          <details v-for="(message, index) in messages" :key="message.id" class="policy-turn" :open="index === messages.length - 1">
            <summary><span class="policy-turn-order">{{ String(index + 1).padStart(2, '0') }}</span><b>{{ message.question }}</b><small v-if="message.created_at">{{ formatDate(message.created_at) }}</small><Icon name="chevron" :size="17" /></summary>
            <div class="policy-turn-body">
              <p v-if="message.response.retrieval?.used_context" class="policy-context"><Icon name="history" :size="15" /><span>结合上一问“{{ message.response.retrieval.previous_question }}”查找依据</span></p>
              <div class="query-result-title"><Icon :name="message.response.citations.length ? 'check' : 'info'" :size="20" /><b>{{ message.response.answer }}</b></div>
              <article v-for="(hit, i) in message.response.citations" :key="`${hit.source.id}-${hit.location}`" class="citation-card"><div><span class="citation-number">{{ String(i + 1).padStart(2,'0') }}</span><span class="mini-label">{{ hit.location }}</span><span v-if="hit.source.is_school_policy" class="mini-label">学校细则</span></div><blockquote>{{ hit.text }}</blockquote><div class="citation-source"><b>{{ hit.source.title }}</b><span>{{ hit.source.publisher }} · {{ hit.source.version }}</span><a :href="hit.source.source_url" target="_blank" rel="noopener noreferrer">查看原文来源<Icon name="external" :size="15" /></a></div></article>
              <p class="caption">{{ message.response.notice }}</p>
            </div>
          </details>
        </div>
        <div v-else class="policy-placeholder"><Icon name="shield" :size="21" /><span>查到原文后，可以继续问“那临时岗位呢”。<br>本校细则尚未提供时，请向学校资助中心确认。</span></div>
      </section>
      <aside class="policy-side">
        <section class="panel policy-history-panel"><div class="panel-heading"><h3>我的政策会话</h3><Icon name="history" :size="19" /></div><p class="caption policy-history-caption">历史仅本人可见；新会话从新的问题开始。</p><div v-if="conversations.length" class="policy-history-list"><button v-for="item in conversations" :key="item.conversation_id" class="policy-history-item" :class="{ selected: item.conversation_id === conversationId }" :disabled="busy || loading" :aria-current="item.conversation_id === conversationId ? 'true' : undefined" @click="openConversation(item.conversation_id)"><b>{{ item.title }}</b><span>{{ item.message_count }} 次提问 · {{ formatDate(item.updated_at) }}</span><small>{{ item.last_question }}</small></button></div><p v-else class="policy-history-empty">提问后，会话会保存在这里。</p></section>
        <section class="panel knowledge-panel"><div class="panel-heading"><h3>已收录原文</h3><span class="mini-label">{{ docs.length }} 份</span></div><button v-for="doc in docs" :key="doc.id" class="knowledge-doc" @click="read(doc)"><Icon name="book" :size="23" /><b>{{ doc.title }}</b><span>{{ doc.version }} · {{ doc.section_count }} 段原文</span><small>核验：{{ formatDate(doc.verified_at) }}</small><span class="green-text"><Icon name="shield" :size="14" />已核验来源</span></button><div v-if="!docs.length" class="empty-state"><Icon name="book" :size="30" /><h3>尚未收录已核验原文</h3><p>资助中心取得可靠来源并核验后导入，查询会如实提示缺少依据。</p></div><div class="notice neutral"><Icon name="info" /><span>按已收录原文检索。请核对发布版本、适用范围和学校正式细则。</span></div></section>
      </aside>
    </div>
    <el-drawer v-model="docDrawer" title="已核验政策原文" size="min(600px, 100%)"><div v-if="docDetail"><h2>{{ docDetail.title }}</h2><p class="muted">{{ docDetail.publisher }} · {{ docDetail.version }}</p><a class="text-button" :href="docDetail.source_url" target="_blank" rel="noopener noreferrer">打开原文来源<Icon name="external" :size="16" /></a><p class="caption">{{ docDetail.verification_note }}<br>核验时间：{{ formatDate(docDetail.verified_at) }}</p><section v-for="section in docDetail.sections" :key="section.location" class="original-section"><h3>{{ section.location }}</h3><p>{{ section.text }}</p></section></div></el-drawer>
    <el-dialog v-model="importing" title="导入已核验政策原文" width="640px"><form @submit.prevent="save"><div class="notice"><Icon name="shield" /><span>请先核对官方原文及版本。此操作记录为资助中心已完成核验，不会自动验证网址内容。</span></div><label class="field">政策标题<input v-model.trim="form.title" required minlength="2" maxlength="200"></label><div class="form-grid"><label class="field">发布主体<input v-model.trim="form.publisher" required minlength="2" maxlength="100"></label><label class="field">版本<input v-model.trim="form.version" required maxlength="100" placeholder="例如：2018年修订"></label></div><label class="field">实际来源链接<input v-model.trim="form.source_url" type="url" required maxlength="500" placeholder="https://官方发布原文网址"></label><label class="field">已完成核验的时间<input v-model="form.verified_at" type="datetime-local" required></label><label class="field">核验依据和结论<textarea v-model.trim="form.verification_note" required minlength="5" rows="2" maxlength="2000"></textarea></label><label class="field">条款与原文（每行一段，定位 | 原文）<textarea v-model="form.text" rows="6" required placeholder="第二十一条 | 粘贴实际原文；不要填写自行生成的政策内容"></textarea></label><label class="checkbox-label"><input v-model="form.is_school_policy" type="checkbox">这是已核验的本校正式细则</label><div class="modal-actions"><button type="button" class="btn btn-secondary" @click="importing = false">取消</button><button class="btn btn-primary" :disabled="saving">核验完成并导入</button></div></form></el-dialog>
  </div>
</template>
