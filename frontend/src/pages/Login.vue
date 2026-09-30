<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, signIn } from '../api'
import Icon from '../components/Icon.vue'
import CampusScene from '../components/CampusScene.vue'
const router = useRouter(), registering = ref(false), busy = ref(false), error = ref(''), success = ref('')
const username = ref('student'), password = ref('Demo@2026'), displayName = ref(''), chosen = ref('student')
const roles = [{ username: 'student', label: '学生', icon: 'graduate', description: '发现适合自己的课余岗位' },
  { username: 'library', label: '用工单位', icon: 'building', description: '发布岗位与管理申请' },
  { username: 'aid', label: '资助中心', icon: 'shield', description: '确认困难等级与核实工时' },
  { username: 'admin_demo', label: '管理员', icon: 'settings', description: '启用账号与管理系统' }]
function choose(role) { chosen.value = role.username; username.value = role.username; password.value = 'Demo@2026'; error.value = '' }
function toggle() { registering.value = !registering.value; error.value = ''; success.value = ''; username.value = registering.value ? '' : 'student'; password.value = registering.value ? '' : 'Demo@2026' }
async function submit() {
  error.value = ''; success.value = ''; busy.value = true
  try {
    if (registering.value) { const result = await api('/auth/register', { method: 'POST', body: { username: username.value, password: password.value, display_name: displayName.value } }); success.value = result.message }
    else { await signIn(username.value, password.value); router.push('/overview') }
  } catch (e) { error.value = e.message === 'Failed to fetch' ? '服务未连接，请先运行启动体验' : e.message }
  finally { busy.value = false }
}
</script>
<template>
  <div class="login-page">
    <section class="login-story"><a href="/login" class="brand"><span class="brand-icon"><Icon name="leaf" :size="26" /></span><span><b>青禾</b><small>校园勤工助学</small></span></a>
      <div class="login-story-body"><span class="eyebrow"><i></i>课余有方向，成长有回响</span><h1>在校园里，<br>找到属于你的<br><em>另一种成长。</em></h1><p>把合适的时间，留给合适的机会。<br>从一份课余岗位开始，让努力被看见。</p><CampusScene /><div class="login-benefits"><span><Icon name="check" :size="16" />四角色协同</span><span><Icon name="check" :size="16" />匹配有依据</span><span><Icon name="check" :size="16" />工时可追溯</span></div></div>
      <small class="login-story-footer">QINGHE CAMPUS · GROW AT YOUR OWN PACE</small>
    </section>
    <section class="login-form-side"><div class="login-form-wrap"><span class="section-kicker">WELCOME TO QINGHE</span><h2>{{ registering ? '创建学生账号' : '欢迎来到青禾' }}</h2><p class="muted">{{ registering ? '注册后需由系统管理员启用，再开始你的体验。' : '选择一个演示身份，开始体验校园服务。' }}</p>
      <div v-if="!registering" class="role-options"><button v-for="role in roles" :key="role.username" type="button" :class="{ selected: chosen === role.username }" @click="choose(role)"><Icon :name="role.icon" :size="23" /><b>{{ role.label }}</b><i v-if="chosen === role.username"><Icon name="check" :size="12" /></i></button></div>
      <p v-if="!registering" class="role-description">{{ roles.find(r => r.username === chosen)?.description }}</p>
      <form @submit.prevent="submit">
        <label v-if="registering" class="field">显示姓名<input v-model.trim="displayName" placeholder="体验时显示的名字" required maxlength="50" autocomplete="nickname"></label>
        <label class="field">{{ registering ? '学生账号' : '账号' }}<input v-model.trim="username" placeholder="字母、数字或下划线" required minlength="3" maxlength="32" autocomplete="username"></label>
        <label class="field">密码<input v-model="password" type="password" :placeholder="registering ? '至少 8 位' : '输入登录密码'" required :minlength="registering ? 8 : 1" maxlength="100" :autocomplete="registering ? 'new-password' : 'current-password'"></label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p><p v-if="success" class="form-success" role="status">{{ success }}</p>
        <button class="btn btn-primary login-submit" :disabled="busy">{{ busy ? '请稍候…' : registering ? '注册学生账号' : '进入我的工作台' }}<Icon v-if="!busy" name="right" :size="19" /></button>
      </form>
      <div class="login-register"><span>{{ registering ? '已有账号？' : '想体验注册流程？' }}</span><button class="text-button" @click="toggle">{{ registering ? '返回登录' : '创建学生账号' }}<Icon name="arrow" :size="15" /></button></div>
      <div class="demo-note"><Icon name="info" :size="18" /><span>本地体验版 · 全部为模拟账号与业务数据<br>演示密码：Demo@2026</span></div>
    </div><div class="login-side-footer">让课余时间，成为成长的机会。<span>v0.2</span></div></section>
  </div>
</template>
