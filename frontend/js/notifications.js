// 站内通知：别人变更了我的权限时收到通知（邀请、审批结果、调整、撤销、知识库转让）。
// 顶部铃铛显示未读数并每 30 秒轮询一次，有新通知时右上角弹出提醒。
// 与 permissions.js 一样在页面组件之前加载，ref / ElMessage 等只能在函数运行时使用。

const NOTICE_POLL_MS = 30000
const NOTICE_TYPE = {
  perm_invited: { title: '收到权限邀请', type: 'info' },
  perm_approved: { title: '申请已通过', type: 'success' },
  perm_rejected: { title: '申请被驳回', type: 'warning' },
  perm_changed: { title: '权限已调整', type: 'info' },
  perm_revoked: { title: '权限被撤销', type: 'warning' },
  perm_withdrawn: { title: '请求已撤回', type: 'warning' },
  kb_transferred: { title: '知识库转让', type: 'success' },
}

const noticeStore = Vue.reactive({ unread: 0, lastId: null })
let noticeTimer = null

const noticeText = (n) => `${n.actor?.nickname || '有人'} ${n.content}`

// 拉取未读通知：更新角标；比上次看到的更新的通知弹出提醒（首次加载只记录位置，不弹）
const pollNotifications = async () => {
  if (!auth.user) return
  try {
    const page = await api.listNotifications({ unread_only: true, size: 5 }, { silent: true })
    noticeStore.unread = page.unread
    const newest = page.items[0]?.id ?? 0
    if (noticeStore.lastId !== null) {
      page.items.filter((n) => n.id > noticeStore.lastId).reverse().forEach((n) => {
        ElementPlus.ElNotification({
          title: NOTICE_TYPE[n.type]?.title || '通知', message: noticeText(n), type: NOTICE_TYPE[n.type]?.type || 'info',
          onClick: () => openNotice(n),
        })
      })
      // 权限变了：刷新“权限中心”角标（可能多了一条邀请）
      if (newest > noticeStore.lastId) refreshPermissionBadge()
    }
    noticeStore.lastId = Math.max(noticeStore.lastId ?? 0, newest)
  } catch {
    // 轮询失败不打扰用户，下次再试
  }
}

const startNotificationPolling = () => {
  if (noticeTimer) return
  pollNotifications()
  noticeTimer = setInterval(pollNotifications, NOTICE_POLL_MS)
}

const stopNotificationPolling = () => {
  clearInterval(noticeTimer)
  noticeTimer = null
  Object.assign(noticeStore, { unread: 0, lastId: null })
}

// 点击通知：标记已读并跳转到相关知识库 / 文档（对象已删除时只标记已读）
const openNotice = async (n) => {
  if (!n.is_read) {
    n.is_read = true
    noticeStore.unread = Math.max(0, noticeStore.unread - 1)
    api.readNotification(n.id).catch(() => {})
  }
  const link = targetLink(n)
  if (link) router.push(link)
}

const readAllNotices = async (items = []) => {
  await api.readAllNotifications()
  items.forEach((n) => { n.is_read = true })
  noticeStore.unread = 0
}

// 单条通知
const NoticeItem = {
  props: { n: Object },
  template: `
  <div class="notice-item" :class="{ unread: !n.is_read }" @click="openNotice(n)">
    <span class="notice-dot"></span>
    <div class="notice-main">
      <div><b>{{ n.actor?.nickname || '有人' }}</b> {{ n.content }}</div>
      <div class="muted">{{ NOTICE_TYPE[n.type]?.title }} · {{ formatTime(n.created_at) }}</div>
    </div>
  </div>`,
  setup: () => ({ openNotice, formatTime, NOTICE_TYPE }),
}

// ---------------- 顶部铃铛 ----------------
const NotificationBell = {
  components: { NoticeItem },
  template: `
  <el-popover placement="bottom-end" :width="360" trigger="click" @show="load" popper-class="notice-popover">
    <template #reference>
      <span class="notice-bell" title="通知">
        <el-badge :value="noticeStore.unread" :hidden="!noticeStore.unread" :max="99">🔔</el-badge>
      </span>
    </template>
    <div class="notice-head">
      <b>通知</b>
      <el-button link type="primary" :disabled="!noticeStore.unread" @click="readAll">全部已读</el-button>
    </div>
    <div v-loading="loading" class="notice-list">
      <el-empty v-if="!loading && !items.length" description="暂无通知" :image-size="60" />
      <notice-item v-for="n in items" :key="n.id" :n="n" />
    </div>
    <div class="notice-foot"><el-button link @click="$router.push('/notifications')">查看全部</el-button></div>
  </el-popover>`,
  setup() {
    const items = Vue.ref([])
    const loading = Vue.ref(false)
    const load = async () => {
      loading.value = true
      try {
        const page = await api.listNotifications({ size: 8 })
        items.value = page.items
        noticeStore.unread = page.unread
      } finally {
        loading.value = false
      }
    }
    const readAll = () => readAllNotices(items.value)
    return { noticeStore, items, loading, load, readAll }
  },
}

// ---------------- 通知列表页 ----------------
const NotificationsPage = {
  components: { NoticeItem },
  template: `
  <div class="page perm-page">
    <div class="kb-toolbar">
      <h2 class="page-title">通知</h2>
      <el-radio-group v-model="query.unreadOnly" @change="search">
        <el-radio-button :value="false">全部</el-radio-button>
        <el-radio-button :value="true">未读 {{ noticeStore.unread || '' }}</el-radio-button>
      </el-radio-group>
      <el-button :disabled="!noticeStore.unread" @click="readAll">全部已读</el-button>
    </div>
    <el-card shadow="never" v-loading="loading">
      <el-empty v-if="!loading && !items.length" :description="query.unreadOnly ? '没有未读通知' : '暂无通知'" />
      <notice-item v-for="n in items" :key="n.id" :n="n" />
      <el-pagination v-if="total > query.size" class="pager" background layout="total, prev, pager, next"
        :total="total" v-model:current-page="query.page" :page-size="query.size" @current-change="load" />
    </el-card>
  </div>`,
  setup() {
    const items = Vue.ref([])
    const total = Vue.ref(0)
    const loading = Vue.ref(false)
    const query = Vue.reactive({ page: 1, size: 20, unreadOnly: false })
    const load = async () => {
      loading.value = true
      try {
        const page = await api.listNotifications({ page: query.page, size: query.size, unread_only: query.unreadOnly })
        items.value = page.items
        total.value = page.total
        noticeStore.unread = page.unread
      } finally {
        loading.value = false
      }
    }
    const search = () => { query.page = 1; load() }
    const readAll = async () => {
      await readAllNotices(items.value)
      if (query.unreadOnly) search()
    }
    Vue.onMounted(load)
    return { noticeStore, items, total, loading, query, load, search, readAll }
  },
}
