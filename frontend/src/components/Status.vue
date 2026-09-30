<script setup>
import { statusNames } from '../api'
defineProps({ value: String })
</script>
<template><span class="status" :class="`status-${value}`"><i></i>{{ statusNames[value] || value }}</span></template>
