<script setup>
import { Leaf, LayoutDashboard, BriefcaseBusiness, Sparkles, Files, Clock3, BookOpen, UserRound, UsersRound,
  Building2, SlidersHorizontal, ShieldCheck, ArrowUpRight, ArrowRight, ChevronRight, Search, Plus, Menu,
  LogOut, CalendarDays, Check, X, Info, Pencil, RefreshCw, Send, MapPin, Wallet, Hourglass, TrendingUp,
  History, CircleAlert, ExternalLink, Filter, ChartNoAxesCombined, Eye, GraduationCap, CircleHelp } from 'lucide-vue-next'
defineProps({ name: { type: String, default: 'leaf' }, size: { type: Number, default: 20 } })
const icons = { leaf: Leaf, grid: LayoutDashboard, briefcase: BriefcaseBusiness, sparkles: Sparkles, files: Files,
  clock: Clock3, book: BookOpen, user: UserRound, users: UsersRound, building: Building2, settings: SlidersHorizontal,
  shield: ShieldCheck, arrow: ArrowUpRight, right: ArrowRight, chevron: ChevronRight, search: Search, plus: Plus,
  menu: Menu, logout: LogOut, calendar: CalendarDays, check: Check, x: X, info: Info, edit: Pencil, refresh: RefreshCw,
  send: Send, location: MapPin, money: Wallet, hourglass: Hourglass, trend: TrendingUp, history: History,
  alert: CircleAlert, external: ExternalLink, filter: Filter, chart: ChartNoAxesCombined, eye: Eye,
  graduate: GraduationCap, help: CircleHelp }
</script>
<template><component :is="icons[name] || icons.leaf" :size="size" :stroke-width="1.7" aria-hidden="true" /></template>
