<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, perform } from '../api'
import Icon from '../components/Icon.vue'
const units = ref([]), dialog = ref(false), busy = ref(false), loading = ref(true)
const form = reactive({ name: '', description: '', area: 'A' })
async function load() { const result = await perform(() => api('/admin/units')); if (result) units.value = result.items; loading.value = false }
onMounted(load)
function open() { Object.assign(form, { name: '', description: '', area: 'A' }); dialog.value = true }
async function save() { busy.value = true; if (await perform(() => api('/admin/units', { method: 'POST', body: form }), '用工单位已创建，可为其创建管理账号')) { dialog.value = false; await load() }; busy.value = false }
</script>
<template><div><div class="page-heading"><div><p class="section-kicker">CONNECTED CAMPUS SERVICES</p><h1>校园机会的提供者<span class="heading-dot">.</span></h1><p>管理用工单位，单位管理员必须绑定明确的服务范围。</p></div><button class="btn btn-primary" @click="open"><Icon name="plus" :size="18" />新增单位</button></div><el-skeleton v-if="loading" :rows="5" animated /><div v-else class="units-grid"><article v-for="unit in units" :key="unit.id" class="panel unit-card"><span class="unit-mark" :class="`area-${unit.area}`"><Icon name="building" :size="25" /></span><span class="mini-label">{{ unit.area }} 区</span><h3>{{ unit.name }}</h3><p>{{ unit.description || '暂无单位介绍' }}</p><router-link to="/users" class="text-button">管理单位账号<Icon name="right" :size="16" /></router-link></article></div><el-dialog v-model="dialog" title="新增用工单位" width="460px"><form @submit.prevent="save"><label class="field">单位名称<input v-model.trim="form.name" required minlength="2" maxlength="100"></label><label class="field">所在区域<select v-model="form.area"><option v-for="area in ['A','B','C']" :key="area">{{ area }}</option></select></label><label class="field">单位介绍<textarea v-model.trim="form.description" rows="3" maxlength="1000"></textarea></label><div class="modal-actions"><button class="btn btn-secondary" type="button" @click="dialog = false">取消</button><button class="btn btn-primary" :disabled="busy">保存单位</button></div></form></el-dialog></div></template>
