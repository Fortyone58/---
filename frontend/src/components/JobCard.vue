<script setup>
import { formatSlots, state } from '../api'
import Icon from './Icon.vue'
import Status from './Status.vue'
defineProps({ job: Object, score: { type: [String, Number], default: null }, manage: Boolean })
defineEmits(['view'])
</script>
<template>
  <article class="job-card" :data-job-id="job.id">
    <div class="job-top"><span class="unit-mark" :class="`area-${job.area}`"><Icon :name="job.unit_name.includes('图书') ? 'book' : 'building'" /></span>
      <span class="unit-label">{{ job.unit_name }}</span><Status v-if="manage || job.status !== 'published'" :value="job.status" /><span v-else class="mini-label">{{ job.category === 'fixed' ? '固定岗' : '临时岗' }}</span>
    </div>
    <button class="job-title" @click="$emit('view', job)">{{ job.title }}<Icon name="arrow" :size="18" /></button>
    <div class="job-wage"><span>¥</span><strong>{{ Number(job.wage) }}</strong><small>/ {{ job.category === 'fixed' ? '月基准' : '小时' }}</small></div>
    <p class="job-location"><Icon name="location" :size="15" />{{ job.location }}</p>
    <p class="job-time"><Icon name="clock" :size="15" />{{ formatSlots(job.slots) || '工作时段待补充' }}</p>
    <div class="tags"><span v-for="skill in job.skills" :key="skill">{{ state.meta?.skills[skill] || skill }}</span><span v-if="!job.skills.length">不限技能</span></div>
    <div class="job-footer"><span class="remaining"><i></i>余 {{ job.remaining }} / {{ job.quota }} 个名额</span><span v-if="score !== null" class="match-mini">{{ Number(score).toFixed(0) }}% 匹配</span>
      <button v-else class="text-button" @click="$emit('view', job)">{{ manage ? '查看详情' : '查看岗位' }}<Icon name="right" :size="16" /></button></div>
    <div v-if="manage" class="job-management"><slot /></div>
  </article>
</template>
