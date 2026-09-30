import { createRouter, createWebHistory } from 'vue-router'
import { state, restoreSession } from './api'
import Shell from './components/Shell.vue'

const all = ['student', 'unit', 'aid', 'admin']
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./pages/Login.vue') },
    { path: '/', component: Shell, children: [
      { path: '', redirect: '/overview' },
      { path: 'overview', component: () => import('./pages/Overview.vue'), meta: { title: '工作台', roles: all } },
      { path: 'jobs', component: () => import('./pages/Jobs.vue'), meta: { title: '岗位中心', roles: ['student', 'unit', 'admin', 'aid'] } },
      { path: 'matching', component: () => import('./pages/Matching.vue'), meta: { title: '为我匹配', roles: ['student'] } },
      { path: 'applications', component: () => import('./pages/Applications.vue'), meta: { title: '申请记录', roles: all } },
      { path: 'workhours', component: () => import('./pages/Workhours.vue'), meta: { title: '工时与薪酬', roles: all } },
      { path: 'policies', component: () => import('./pages/Policies.vue'), meta: { title: '政策原文查询', roles: all } },
      { path: 'profile', component: () => import('./pages/Profile.vue'), meta: { title: '我的档案', roles: ['student'] } },
      { path: 'users', component: () => import('./pages/Users.vue'), meta: { title: '账号与档案', roles: ['admin', 'aid'] } },
      { path: 'units', component: () => import('./pages/Units.vue'), meta: { title: '用工单位', roles: ['admin'] } },
      { path: 'settings', component: () => import('./pages/Settings.vue'), meta: { title: '匹配参数', roles: ['admin'] } },
      { path: 'audit', component: () => import('./pages/Audit.vue'), meta: { title: '操作审计', roles: ['admin'] } },
    ] },
    { path: '/:pathMatch(.*)*', redirect: '/overview' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
router.beforeEach(async to => {
  if (to.path === '/login') return true
  if (!state.user && !(await restoreSession())) return '/login'
  if (to.meta.roles && !to.meta.roles.includes(state.user.role)) return '/overview'
  return true
})
window.addEventListener('qinghe-auth-expired', () => router.replace('/login'))
export default router
