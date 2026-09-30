import { createApp } from 'vue'
import { ElConfigProvider, ElDialog, ElDrawer, ElDropdown, ElDropdownItem, ElDropdownMenu,
  ElPopconfirm, ElSkeleton } from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)
for (const component of [ElConfigProvider, ElDialog, ElDrawer, ElDropdown, ElDropdownItem, ElDropdownMenu,
  ElPopconfirm, ElSkeleton]) app.component(component.name, component)
app.use(router).mount('#app')
