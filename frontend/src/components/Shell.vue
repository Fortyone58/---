<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { state, roleNames, clearSession, signIn, perform } from '../api'
import Icon from './Icon.vue'
const route = useRoute(), router = useRouter()
const mobileMenu = ref(false), help = ref(false), switching = ref(false)
const menu = computed(() => {
  const role = state.user?.role
  const base = [{ path: '/overview', name: '工作台', icon: 'grid' }]
  if (role === 'student') return [...base,
    { path: '/jobs', name: '发现岗位', icon: 'briefcase' }, { path: '/matching', name: '为我匹配', icon: 'sparkles' },
    { path: '/applications', name: '我的申请', icon: 'files' }, { path: '/workhours', name: '工时与薪酬', icon: 'clock' },
    { path: '/policies', name: '政策原文查询', icon: 'book' }, { path: '/profile', name: '我的档案', icon: 'user', group: true }]
  if (role === 'unit') return [...base,
    { path: '/jobs', name: '岗位管理', icon: 'briefcase' }, { path: '/applications', name: '申请审核', icon: 'files' },
    { path: '/workhours', name: '工时与薪酬', icon: 'clock' }, { path: '/policies', name: '政策原文查询', icon: 'book' }]
  if (role === 'aid') return [...base,
    { path: '/users', name: '学生困难确认', icon: 'users' }, { path: '/workhours', name: '异常工时核实', icon: 'clock' },
    { path: '/jobs', name: '全校岗位', icon: 'briefcase' }, { path: '/applications', name: '申请总览', icon: 'files' },
    { path: '/policies', name: '政策原文与维护', icon: 'book' }]
  return [...base,
    { path: '/users', name: '账号管理', icon: 'users' }, { path: '/units', name: '用工单位', icon: 'building' },
    { path: '/jobs', name: '岗位总览', icon: 'briefcase' }, { path: '/applications', name: '申请总览', icon: 'files' },
    { path: '/workhours', name: '工时与薪酬', icon: 'clock' }, { path: '/settings', name: '匹配参数', icon: 'settings', group: true },
    { path: '/audit', name: '操作审计', icon: 'shield' }, { path: '/policies', name: '政策原文查询', icon: 'book' }]
})
const dateText = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())
async function switchAccount(username) {
  if (username === 'logout') { clearSession(); router.push('/login'); return }
  switching.value = true
  await perform(async () => { await signIn(username, 'Demo@2026'); router.replace('/overview') }, '已切换演示身份')
  switching.value = false
}
</script>
<template>
  <div v-if="state.user" class="app-shell">
    <div v-if="mobileMenu" class="sidebar-overlay" @click="mobileMenu = false"></div>
    <aside class="sidebar" :class="{ 'is-open': mobileMenu }">
      <router-link to="/overview" class="brand" @click="mobileMenu = false"><span class="brand-icon"><Icon name="leaf" :size="24" /></span><span><b>青禾</b><small>校园勤工助学</small></span></router-link>
      <span class="nav-caption">{{ roleNames[state.user.role] }}空间</span>
      <nav aria-label="主导航"><router-link v-for="item in menu" :key="item.path" :to="item.path" class="nav-link" :class="{ 'nav-group': item.group }" @click="mobileMenu = false"><Icon :name="item.icon" :size="19" /><span>{{ item.name }}</span><i v-if="route.path === item.path"></i></router-link></nav>
      <div class="sidebar-bottom"><div class="help-card"><span class="help-card-icon"><Icon name="graduate" :size="26" /></span><b>第一次使用青禾？</b><p>从发现岗位到记录成长，<br>了解完整体验流程。</p><button class="text-button" @click="help = true">查看使用指南<Icon name="right" :size="16" /></button></div><div class="sidebar-foot"><span class="live-dot"></span>本地体验版 <span>v0.1</span></div></div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><div class="breadcrumb"><button class="icon-button mobile-toggle" aria-label="打开导航菜单" @click="mobileMenu = !mobileMenu"><Icon name="menu" /></button><span>校园服务</span><Icon name="chevron" :size="14" /><strong>{{ route.meta.title }}</strong></div>
        <div class="topbar-right"><span class="date-text"><Icon name="calendar" :size="16" />{{ dateText }}</span><button class="icon-button top-help" aria-label="查看使用指南" @click="help = true"><Icon name="help" /></button>
          <el-dropdown trigger="click" @command="switchAccount"><button class="account-button" :disabled="switching"><span class="avatar">{{ state.user.display_name.slice(0, 1) }}</span><span class="account-name"><b>{{ state.user.display_name }}</b><small>{{ roleNames[state.user.role] }}</small></span><Icon name="chevron" :size="14" /></button><template #dropdown><el-dropdown-menu><el-dropdown-item disabled>切换演示身份</el-dropdown-item><el-dropdown-item command="student">学生 · 林同学</el-dropdown-item><el-dropdown-item command="library">单位 · 图书馆</el-dropdown-item><el-dropdown-item command="lab">单位 · 数字校园实验室</el-dropdown-item><el-dropdown-item command="aid">资助中心</el-dropdown-item><el-dropdown-item command="admin_demo">系统管理员</el-dropdown-item><el-dropdown-item divided command="logout">退出 / 登录其他账号</el-dropdown-item></el-dropdown-menu></template></el-dropdown>
        </div>
      </header>
      <main class="page-content"><router-view :key="`${state.user.id}-${route.path}`" /></main>
      <footer class="page-footer"><span>青禾 · 让每一份课余努力，都有所收获。</span><span>模拟业务数据 · 工资为估算 · 规则为原型约定</span></footer>
    </div>
    <el-drawer v-model="help" title="欢迎使用青禾" size="min(460px, 100%)">
      <p class="muted">这是可保存数据的本地原型。右上角可以切换演示身份，体验完整业务流。</p>
      <ol class="guide-list"><li><b>学生：发现与申请</b><p>在“发现岗位”打开详情，填写理由提交。我的申请可查看进度，待审核或已批准时可以撤销。</p></li><li><b>用工单位：审核与上岗</b><p>切换图书馆或实验室账号，审核本单位申请，依次批准、确认上岗，再登记当天工时。</p></li><li><b>资助中心：确认与核实</b><p>确认学生演示困难等级；对超限工时核实。核实后的待核金额会计入正常估算。</p></li><li><b>系统管理员：启用与追踪</b><p>启用新注册学生，创建单位账号，调整匹配权重，在操作审计查看写入记录。</p></li></ol>
      <div class="notice"><Icon name="info" /><span>初始工时在 2026 年 9 月。修改会保存；想恢复初始场景，先停止服务再双击“重置演示数据”。</span></div>
    </el-drawer>
  </div>
</template>
