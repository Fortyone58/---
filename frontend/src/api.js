import { reactive } from 'vue'
import { ElMessage } from 'element-plus'

export const state = reactive({ user: null, meta: null })

export async function api(path, options = {}) {
  const token = sessionStorage.getItem('qinghe-token')
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers },
    ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
  })
  let data
  try { data = await response.json() } catch { throw new Error('服务暂时没有响应，请检查是否已启动') }
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') {
      clearSession()
      window.dispatchEvent(new Event('qinghe-auth-expired'))
    }
    const detail = data.details?.map(d => `${d.field}：${d.message}`).join('；')
    const error = new Error(detail || data.message || '操作未完成，请重试')
    error.status = response.status
    throw error
  }
  return data
}

export async function signIn(username, password) {
  const result = await api('/auth/login', { method: 'POST', body: { username, password } })
  sessionStorage.setItem('qinghe-token', result.access_token)
  state.user = result.user
  state.meta = await api('/meta')
  return result.user
}

export function clearSession() {
  sessionStorage.removeItem('qinghe-token')
  state.user = null
  state.meta = null
}

export async function restoreSession() {
  if (!sessionStorage.getItem('qinghe-token')) return false
  try {
    const [user, meta] = await Promise.all([api('/auth/me'), api('/meta')])
    state.user = user
    state.meta = meta
    return true
  } catch { clearSession(); return false }
}

export async function perform(task, message) {
  try {
    const result = await task()
    if (message) ElMessage.success(message)
    return result
  } catch (error) {
    ElMessage.error(error.message === 'Failed to fetch' ? '无法连接服务，请运行启动体验' : error.message)
    return null
  }
}

export const roleNames = { student: '学生', unit: '用工单位', aid: '资助中心', admin: '系统管理员' }
export const statusNames = {
  pending_review: '待审核', approved: '已批准', onboard: '已上岗', finished: '已结束',
  rejected: '已驳回', withdrawn: '已撤销', published: '招聘中', draft: '草稿', closed: '已关闭',
  normal: '正常', pending: '待核实', verified: '异常已核实', active: '已启用', pending_activation: '待启用',
}
export const partNames = { time: '时间契合', hardship: '困难优先', skills: '技能匹配', location: '地点便利' }
export const dayNames = ['', '周一', '周二', '周三', '周四', '周五', '周六', '周日']
export function formatSlots(slots) { return (slots || []).map(s => `${dayNames[s.day]} ${s.start}–${s.end}`).join(' / ') }
export function formatDate(value) { return value ? value.replace('T', ' ').slice(0, 16) : '—' }
export function money(value) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }
export function today() { return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date()) }
