<script setup>
import { dayNames } from '../api'
import Icon from './Icon.vue'
const model = defineModel({ type: Array, default: () => [] })
function add() { model.value = [...model.value, { day: 1, start: '14:00', end: '16:00' }] }
function remove(index) { model.value = model.value.filter((_, i) => i !== index) }
</script>
<template>
  <div class="slot-editor">
    <div v-for="(slot, index) in model" :key="index" class="slot-row">
      <select v-model.number="slot.day" :aria-label="`时段${index + 1}星期`"><option v-for="d in 7" :key="d" :value="d">{{ dayNames[d] }}</option></select>
      <input v-model="slot.start" type="time" :aria-label="`时段${index + 1}开始时间`" required><span>至</span>
      <input v-model="slot.end" type="time" :aria-label="`时段${index + 1}结束时间`" required>
      <button type="button" class="icon-button" :aria-label="`删除时段${index + 1}`" @click="remove(index)"><Icon name="x" :size="17" /></button>
    </div>
    <button type="button" class="text-button" @click="add"><Icon name="plus" :size="17" />添加每周时段</button>
  </div>
</template>
