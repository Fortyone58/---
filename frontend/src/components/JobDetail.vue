<script setup>
import { ref, watch } from 'vue'
import { api, perform, state, formatSlots, statusNames } from '../api'
import Icon from './Icon.vue'
import Status from './Status.vue'
const props = defineProps({ job: Object })
const show = defineModel({ type: Boolean, default: false })
const emit = defineEmits(['changed'])
const reason = ref(''), busy = ref(false)
watch(show, () => { reason.value = '' })
async function apply() {
  busy.value = true
  const result = await perform(() => api('/applications', { method: 'POST', body: { job_id: props.job.id, reason: reason.value } }), '申请已提交，请在我的申请查看进度')
  if (result) { show.value = false; emit('changed') }
  busy.value = false
}
</script>
<template>
  <el-drawer v-model="show" title="岗位详情" size="min(520px, 100%)">
    <div v-if="job" class="job-detail"><div class="detail-unit"><span class="unit-mark" :class="`area-${job.area}`"><Icon name="building" /></span>{{ job.unit_name }}<Status :value="job.status" /></div><h2>{{ job.title }}</h2><div class="job-wage detail-wage"><span>¥</span><strong>{{ Number(job.wage) }}</strong><small>/ {{ job.category === 'fixed' ? '月薪基准' : '小时' }}</small></div>
      <div class="detail-facts"><div><Icon name="location" /><span><small>工作地点</small>{{ job.location }} · {{ job.area }} 区</span></div><div><Icon name="clock" /><span><small>每周工作时段</small>{{ formatSlots(job.slots) || '待补充' }}</span></div><div><Icon name="users" /><span><small>招聘名额</small>共 {{ job.quota }} 名，当前余 {{ job.remaining }} 名</span></div></div>
      <h3>岗位介绍</h3><p class="preserve-lines muted">{{ job.description }}</p><h3>岗位技能</h3><div class="tags"><span v-for="skill in job.skills" :key="skill">{{ state.meta?.skills[skill] || skill }}</span><span v-if="!job.skills.length">不限技能</span></div>
      <div class="notice"><Icon name="info" /><span>{{ job.category === 'fixed' ? '固定岗按有效月工时折算，40小时封顶至月薪基准。' : '临时岗按有效工时 × 时薪估算。' }}批准后冻结计薪条款；金额为应结算估算值。</span></div>
      <template v-if="state.user.role === 'student'"><div v-if="job.my_status" class="notice neutral"><Icon name="files" /><span>你最近的申请：{{ statusNames[job.my_status] }}</span></div><form v-if="job.can_apply" class="apply-form" @submit.prevent="apply"><label class="field">申请理由<textarea v-model.trim="reason" rows="4" required minlength="5" maxlength="1000" placeholder="介绍你的相关技能、可用时间与申请意愿（至少5字）"></textarea></label><button class="btn btn-primary full-width" :disabled="busy">{{ busy ? '提交中…' : '提交岗位申请' }}<Icon name="send" :size="18" /></button><p class="caption">提交后由用工单位审核，待审核不会占用名额。</p></form><div v-else class="notice neutral"><Icon name="info" /><span>{{ job.apply_blocked }}</span></div></template>
    </div>
  </el-drawer>
</template>
