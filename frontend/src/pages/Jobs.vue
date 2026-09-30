<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, perform, state } from '../api'
import Icon from '../components/Icon.vue'
import JobCard from '../components/JobCard.vue'
import JobDetail from '../components/JobDetail.vue'
import SlotEditor from '../components/SlotEditor.vue'
const route = useRoute(), manage = computed(() => state.user.role === 'unit')
const jobs = ref([]), loading = ref(true), busy = ref(false), error = ref('')
const filters = reactive({ keyword: '', area: '', category: '', status: state.user.role === 'student' ? 'published' : '' })
const details = ref(false), selected = ref(null), editor = ref(false), editId = ref(null), closing = ref(null)
const form = reactive({ title: '', description: '', location: '', area: 'A', category: 'temporary', wage: 18, quota: 2, slots: [], skills: [] })
const skillOptions = computed(() => Object.entries(state.meta?.skills || {}).filter(([key]) => !key.startsWith('s1') && !key.startsWith('s2')))
async function load() {
  loading.value = true
  await perform(async () => { jobs.value = (await api(`/jobs?${new URLSearchParams(filters)}`)).items })
  loading.value = false
}
async function showJob(job) { selected.value = await perform(() => api(`/jobs/${job.id}`)); if (selected.value) details.value = true }
async function openFromQuery() { if (route.query.job) await showJob({ id: route.query.job }) }
onMounted(async () => { await load(); await openFromQuery() })
watch(() => route.query.job, openFromQuery)
function edit(job = null) {
  editId.value = job?.id || null; error.value = ''
  Object.assign(form, job ? { title: job.title, description: job.description, location: job.location, area: job.area, category: job.category, wage: Number(job.wage), quota: job.quota, slots: job.slots.map(s => ({ ...s })), skills: [...job.skills] } :
    { title: '', description: '', location: '', area: 'A', category: 'temporary', wage: 18, quota: 2, slots: [{ day: 1, start: '14:00', end: '16:00' }], skills: [] })
  editor.value = true
}
async function save() {
  busy.value = true
  const result = await perform(() => api(editId.value ? `/jobs/${editId.value}` : '/jobs', { method: editId.value ? 'PUT' : 'POST', body: form }), editId.value ? '岗位已更新；已批准申请的工资不受影响' : '草稿已创建，可在列表中发布')
  if (result) { editor.value = false; await load() }
  busy.value = false
}
async function act(job, action) {
  busy.value = true
  const result = await perform(() => api(`/jobs/${job.id}/actions`, { method: 'POST', body: { action } }), action === 'publish' ? '岗位已发布' : '岗位已主动关闭')
  if (result) { closing.value = null; await load() }
  busy.value = false
}
async function remove(job) {
  busy.value = true
  if (await perform(() => api(`/jobs/${job.id}`, { method: 'DELETE' }), '草稿已删除')) await load()
  busy.value = false
}
</script>
<template>
  <div><div class="page-heading"><div><p class="section-kicker">CAMPUS OPPORTUNITIES</p><h1>{{ manage ? '管理校园机会' : state.user.role === 'student' ? '发现适合你的岗位' : '全校岗位总览' }}<span class="heading-dot">.</span></h1><p>{{ manage ? '发布岗位、调整名额，招聘批次与历史记录清晰留存。' : '从一份课余工作开始，积累属于自己的实践经验。' }}</p></div><button v-if="manage" class="btn btn-primary" @click="edit()"><Icon name="plus" :size="18" />创建岗位</button><router-link v-else-if="state.user.role === 'student'" to="/matching" class="btn btn-secondary"><Icon name="sparkles" :size="18" />看看我的匹配</router-link></div>
    <form class="filter-bar" @submit.prevent="load"><label class="search-field"><Icon name="search" :size="18" /><input v-model.trim="filters.keyword" aria-label="搜索岗位" placeholder="搜索岗位名称…"></label><select v-model="filters.area" aria-label="工作区域" @change="load"><option value="">全部区域</option><option v-for="area in ['A','B','C']" :key="area" :value="area">{{ area }} 区</option></select><select v-model="filters.category" aria-label="岗位类型" @change="load"><option value="">全部类型</option><option value="temporary">临时岗</option><option value="fixed">固定岗</option></select><select v-model="filters.status" aria-label="招聘状态" @change="load"><option value="">全部状态</option><option value="published">招聘中</option><option value="closed">已关闭</option><option v-if="state.user.role !== 'student'" value="draft">草稿</option></select><button class="btn btn-secondary" :disabled="loading">搜索</button></form>
    <div class="list-caption"><span>共 <strong>{{ jobs.length }}</strong> 个岗位</span><span><span class="live-dot"></span>模拟岗位 · 按招聘批次管理</span></div>
    <el-skeleton v-if="loading" :rows="8" animated />
    <div v-else-if="jobs.length" class="jobs-grid"><JobCard v-for="job in jobs" :key="job.id" :job="job" :manage="manage" @view="showJob"><button class="text-button" :disabled="busy" @click="edit(job)"><Icon name="edit" :size="15" />编辑</button><button v-if="job.status !== 'published'" class="text-button" :disabled="busy || job.remaining === 0" @click="act(job, 'publish')">发布</button><button v-if="job.status === 'published' || job.close_reason === 'full'" class="text-button muted" :disabled="busy" @click="closing = job">关闭</button><el-popconfirm v-if="job.status === 'draft'" title="确认删除这条草稿？" @confirm="remove(job)"><template #reference><button class="text-button danger-text" :disabled="busy">删除</button></template></el-popconfirm><span v-if="job.close_reason" class="caption">{{ job.close_reason === 'full' ? '满额自动关闭' : '单位主动关闭' }}</span></JobCard></div>
    <div v-else class="empty-state panel"><Icon name="briefcase" :size="36" /><h3>暂未找到符合条件的岗位</h3><p>试试其他关键词，或切换招聘状态。</p></div>
    <JobDetail v-model="details" :job="selected" @changed="load" />
    <el-dialog v-model="editor" :title="editId ? '编辑岗位' : '创建岗位草稿'" width="620px"><form class="modal-form" @submit.prevent="save"><label class="field">岗位名称<input v-model.trim="form.title" required minlength="2" maxlength="100"></label><div class="form-grid"><label class="field">工作地点<input v-model.trim="form.location" required minlength="2" maxlength="100"></label><label class="field">区域<select v-model="form.area"><option v-for="area in ['A','B','C']" :key="area">{{ area }}</option></select></label></div><div class="form-grid three"><label class="field">岗位类型<select v-model="form.category"><option value="temporary">临时岗</option><option value="fixed">固定岗</option></select></label><label class="field">{{ form.category === 'fixed' ? '月薪基准（元）' : '时薪（元）' }}<input v-model.number="form.wage" type="number" min="0.01" step="0.01" max="100000" required></label><label class="field">招聘名额<input v-model.number="form.quota" type="number" min="1" max="1000" step="1" required></label></div><label class="field">工作介绍<textarea v-model.trim="form.description" rows="4" required minlength="4" maxlength="4000"></textarea></label><div class="field"><span>每周工作时段</span><SlotEditor v-model="form.slots" /></div><fieldset class="skill-picker"><legend>要求技能（可留空）</legend><label v-for="[key, label] in skillOptions" :key="key"><input v-model="form.skills" type="checkbox" :value="key">{{ label }}</label></fieldset><p class="caption">岗位改薪仅影响后续批准的条款；名额不能低于已录用人数。保存草稿后需明确发布。</p><div class="modal-actions"><button class="btn btn-secondary" type="button" @click="editor = false">取消</button><button class="btn btn-primary" :disabled="busy">{{ busy ? '保存中…' : '保存岗位' }}</button></div></form></el-dialog>
    <el-dialog :model-value="!!closing" title="关闭岗位招聘" width="440px" @close="closing = null"><p>确认主动关闭“{{ closing?.title }}”？</p><p class="muted">新申请与批准会暂停，已录用同学仍可继续上岗与登记。之后需明确重新发布。</p><template #footer><button class="btn btn-secondary" @click="closing = null">取消</button><button class="btn btn-primary" :disabled="busy" @click="act(closing, 'close')">确认关闭</button></template></el-dialog>
  </div>
</template>
