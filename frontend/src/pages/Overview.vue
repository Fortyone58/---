<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, perform, state, money, formatDate } from '../api'
import Icon from '../components/Icon.vue'
import CampusScene from '../components/CampusScene.vue'
import Status from '../components/Status.vue'
const router = useRouter(), stats = ref(null), applications = ref([]), recommendations = ref([]), loading = ref(true)
const role = computed(() => state.user.role)
const hello = computed(() => role.value === 'student' ? `${state.user.display_name}，欢迎回来` : `${state.user.unit_name || state.user.display_name}，欢迎回来`)
const heroes = {
  student: ['让课余时间，', '成为成长的机会。', '适合你的岗位、有据可查的匹配，还有每一份努力的记录。', '发现校园岗位', '/jobs'],
  unit: ['把合适的岗位，', '交给合适的同学。', '从岗位发布到工时登记，让校园用工的每一步清晰有序。', '管理本单位岗位', '/jobs'],
  aid: ['每一份努力，', '都有清晰的记录。', '确认演示困难等级、核实异常工时，让服务与支持及时到位。', '查看待核实工时', '/workhours'],
  admin: ['连接校园机会，', '让服务有序运转。', '启用学生账号、维护用工单位，追踪每一次关键操作。', '管理用户账号', '/users'],
}
const metrics = computed(() => {
  if (!stats.value) return []
  const d = stats.value, p = d.payroll
  return [
    role.value === 'student' ? { title: '正在招聘的岗位', value: d.available_jobs, unit: '个', icon: 'briefcase', note: '发现更多课余机会' } : { title: role.value === 'unit' ? '本单位岗位' : '全校岗位', value: d.job_total, unit: '个', icon: 'briefcase', note: `其中 ${d.available_jobs} 个正在招聘` },
    role.value === 'student' ? { title: '我的申请', value: d.application_total, unit: '次', icon: 'files', note: `${d.applications.pending_review || 0} 次待审核` } : role.value === 'admin' ? { title: '待启用学生', value: d.pending_activation, unit: '人', icon: 'users', note: '启用后才可登录' } : role.value === 'aid' ? { title: '待确认困难等级', value: d.pending_hardship, unit: '人', icon: 'users', note: '演示等级由资助中心确认' } : { title: '待审核申请', value: d.applications.pending_review || 0, unit: '次', icon: 'hourglass', note: '处理本单位学生申请' },
    { title: '9月正常有效工时', value: Number(p.normal_hours), unit: '小时', icon: 'clock', note: `另有 ${Number(p.pending_hours)} 小时待核实` },
    { title: '9月正常薪酬估算', value: money(p.normal_amount), unit: '元', icon: 'money', note: `待核估算 ¥${money(p.pending_amount)}` },
  ]
})
onMounted(async () => {
  await perform(async () => {
    const [d, a] = await Promise.all([api('/stats?month=2026-09'), api('/applications')])
    stats.value = d; applications.value = a.items.slice(0, 4)
    if (role.value === 'student') recommendations.value = (await api('/matching')).items.slice(0, 3)
  })
  loading.value = false
})
</script>
<template>
  <div class="overview-page"><div class="page-heading"><div><p class="section-kicker">YOUR CAMPUS, YOUR POSSIBILITIES</p><h1>{{ hello }}<span class="heading-dot">.</span></h1><p>从这里，开始今天的校园服务。</p></div><span class="outline-pill"><span class="live-dot"></span>{{ role === 'student' ? '学生服务空间' : stats?.scope === '本单位' ? '本单位工作空间' : '全校服务工作空间' }}</span></div>
    <section class="welcome-banner"><div><span class="eyebrow">青禾 · 校园勤工助学</span><h2>{{ heroes[role][0] }}<br><em>{{ heroes[role][1] }}</em></h2><p>{{ heroes[role][2] }}</p><button class="btn btn-primary" @click="router.push(heroes[role][4])">{{ heroes[role][3] }}<Icon name="right" :size="18" /></button></div><CampusScene /><span class="banner-decoration">GROW TOGETHER</span></section>
    <el-skeleton v-if="loading" :rows="5" animated />
    <template v-else-if="stats"><div class="metrics-grid"><article v-for="(metric, i) in metrics" :key="metric.title" class="metric-card"><div class="metric-top"><span>{{ metric.title }}</span><span class="metric-icon" :class="`metric-color-${i}`"><Icon :name="metric.icon" :size="20" /></span></div><p class="metric-value">{{ metric.value }}<small>{{ metric.unit }}</small></p><span class="metric-note">{{ metric.note }}</span></article></div>
      <div class="dashboard-grid"><section class="panel"><div class="panel-heading"><h3>{{ role === 'student' ? '值得看看，为你匹配' : '最近申请' }}</h3><router-link class="text-button" :to="role === 'student' ? '/matching' : '/applications'">查看全部<Icon name="right" :size="16" /></router-link></div>
        <template v-if="role === 'student'"><button v-for="item in recommendations" :key="item.job.id" class="recommend-row" @click="router.push(`/jobs?job=${item.job.id}`)"><span class="unit-mark" :class="`area-${item.job.area}`"><Icon name="briefcase" /></span><span class="row-main"><b>{{ item.job.title }}</b><small>{{ item.job.unit_name }} · {{ item.job.location }}</small></span><span class="row-wage">¥{{ Number(item.job.wage) }}<small>/{{ item.job.category === 'fixed' ? '月基准' : '小时' }}</small></span><span class="score-circle">{{ item.total === null ? '—' : Number(item.total).toFixed(0) }}<small>匹配</small></span></button><div v-if="!recommendations.length" class="empty-state">暂无可申请的推荐岗位</div></template>
        <template v-else><button v-for="app in applications" :key="app.id" class="recommend-row" @click="router.push('/applications')"><span class="small-avatar">{{ app.student_name.slice(0, 1) }}</span><span class="row-main"><b>{{ app.student_name }} · {{ app.job_title }}</b><small>{{ formatDate(app.created_at) }}</small></span><Status :value="app.status" /></button></template>
      </section><section class="panel next-steps"><div class="panel-heading"><h3>{{ role === 'student' ? '我的下一步' : '待办与服务' }}</h3><Icon name="sparkles" :size="18" /></div>
        <template v-if="role === 'student'"><router-link to="/profile" class="next-step"><span class="step-number">01</span><span><b>让匹配更懂你</b><small>维护技能、每周时段与常用区域</small></span><Icon name="chevron" :size="16" /></router-link><router-link to="/applications" class="next-step"><span class="step-number">02</span><span><b>跟进我的申请</b><small>{{ stats.applications.onboard || 0 }} 个岗位已上岗，进度随时可查</small></span><Icon name="chevron" :size="16" /></router-link><router-link to="/policies" class="next-step"><span class="step-number">03</span><span><b>了解勤工助学政策</b><small>查原文，看出处，有依据再判断</small></span><Icon name="chevron" :size="16" /></router-link></template>
        <template v-else><router-link :to="role === 'unit' ? '/applications' : '/users'" class="next-step"><span class="step-number">01</span><span><b>{{ role === 'unit' ? '处理申请审核' : role === 'aid' ? '确认困难演示等级' : '启用待注册学生' }}</b><small>{{ role === 'unit' ? '批准时冻结岗位计薪条款' : '所有关键变更都有审计记录' }}</small></span><Icon name="chevron" :size="16" /></router-link><router-link to="/workhours" class="next-step"><span class="step-number">02</span><span><b>{{ role === 'aid' ? '核实异常工时' : '查看工时与薪酬' }}</b><small>正常与待核金额分别展示</small></span><Icon name="chevron" :size="16" /></router-link><router-link :to="role === 'admin' ? '/audit' : '/policies'" class="next-step"><span class="step-number">03</span><span><b>{{ role === 'admin' ? '查看操作审计' : '查阅政策原文' }}</b><small>有记录，有出处，可追溯</small></span><Icon name="chevron" :size="16" /></router-link></template>
      </section></div><p class="caption overview-caption"><Icon name="info" :size="14" />岗位和申请为当前状态；工时与金额展示固定演示月份 2026-09。应结算估算值不代表实际发薪。</p>
    </template>
  </div>
</template>
