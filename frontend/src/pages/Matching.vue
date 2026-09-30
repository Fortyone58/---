<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, perform, partNames, state } from '../api'
import Icon from '../components/Icon.vue'
import JobCard from '../components/JobCard.vue'
const router = useRouter(), data = ref(null), activeId = ref(null), loading = ref(true)
const selected = computed(() => data.value?.items.find(i => i.job.id === activeId.value) || data.value?.items[0])
onMounted(async () => { data.value = await perform(() => api('/matching')); loading.value = false })
</script>
<template>
  <div><div class="page-heading"><div><p class="section-kicker">A MATCH YOU CAN UNDERSTAND</p><h1>合适，不止是一个分数<span class="heading-dot">.</span></h1><p>时间、技能、困难优先与地点，四个维度解释每一次匹配。</p></div><router-link to="/profile" class="btn btn-secondary"><Icon name="user" :size="18" />完善我的档案</router-link></div>
    <div class="notice"><Icon name="sparkles" /><span>规则匹配，可复现、可解释。当前演示等级 {{ state.user.hardship_confirmed ? state.user.hardship : '待资助中心确认' }}；技能 {{ state.user.skills === null ? '未填写' : `${state.user.skills.length} 项` }}；常用 {{ state.user.area || '未填写' }} 区。困难代码不代表学校正式标准。</span></div>
    <el-skeleton v-if="loading" :rows="8" animated />
    <template v-else-if="data"><div class="weight-strip"><div v-for="(weight, key) in data.weights" :key="key"><span>{{ partNames[key] }}</span><strong>{{ weight }}<small>%</small></strong></div><span class="version-tag">参数版本 v{{ data.version }}</span></div>
      <div v-if="data.items.length" class="matching-layout"><section><div class="list-caption"><span>{{ data.items.length }} 个可申请岗位 · {{ data.items[0]?.total === null ? '资料不足，暂不完整排名' : '按匹配程度排序' }}</span></div><div class="matching-cards"><div v-for="(item, index) in data.items" :key="item.job.id" class="match-card-wrap" :class="{ selected: selected?.job.id === item.job.id }"><div class="match-card-heading"><span>{{ item.total !== null ? `推荐 ${String(index + 1).padStart(2, '0')}` : '待补充资料' }}</span><button class="text-button" @click="activeId = item.job.id">查看匹配依据<Icon name="chevron" :size="15" /></button></div><JobCard :job="item.job" :score="item.total" @view="job => router.push(`/jobs?job=${job.id}`)" /></div></div></section>
        <aside v-if="selected" class="panel score-panel"><span class="section-kicker">WHY THIS MATCH</span><h3>{{ selected.job.title }}</h3><div class="big-score"><strong>{{ selected.total === null ? '—' : Number(selected.total).toFixed(2) }}</strong><small>{{ selected.total === null ? '资料待补充' : '/ 100 综合匹配' }}</small></div><div v-for="(score, key) in selected.parts" :key="key" class="score-part"><div><span>{{ partNames[key] }}</span><b>{{ score === null ? '待补充' : score }}<small v-if="score !== null">分</small></b></div><div class="score-track"><span :style="{ width: `${score || 0}%` }"></span></div><p>{{ key === 'time' ? `覆盖 ${selected.covered_minutes} / ${selected.required_minutes} 分钟` : key === 'hardship' ? (state.user.hardship_confirmed ? `${state.user.hardship} → ${score}分（演示映射）` : '等级待资助中心确认') : key === 'skills' ? (selected.hit_skills.length ? `命中：${selected.hit_skills.map(k => state.meta.skills[k]).join('、')}` : selected.job.skills.length ? '暂无命中技能' : '岗位不限技能') : selected.location_note }}</p></div><div v-if="selected.missing.length" class="notice"><Icon name="info" /><span>{{ selected.missing.join('；') }}。资料补齐后显示完整分数。</span></div><p class="caption">加权总分 = 各分项 × 对应权重之和。仅推荐当前可申请岗位；参数变更后重新计算。</p><button class="btn btn-primary full-width" @click="router.push(`/jobs?job=${selected.job.id}`)">了解这个岗位<Icon name="right" :size="17" /></button></aside>
      </div><div v-else class="empty-state panel"><Icon name="sparkles" :size="36" /><h3>暂无可申请的匹配岗位</h3><p>已申请或暂停招聘的岗位不会出现在推荐中。</p><router-link to="/jobs" class="btn btn-secondary">浏览全部岗位</router-link></div>
    </template>
  </div>
</template>
