const Layout = {
  components: { NotificationBell },
  template: `
  <el-container class="layout">
    <el-header class="header">
      <div class="brand" @click="$router.push(HOME_PATH)">📚 知识库</div>
      <el-menu mode="horizontal" :ellipsis="false" router :default-active="activeMenu" class="nav">
        <el-menu-item index="/kb">知识库</el-menu-item>
        <el-menu-item index="/permissions">
          <el-badge :value="permissionBadge.count" :hidden="!permissionBadge.count" :max="99" class="menu-badge">权限中心</el-badge>
        </el-menu-item>
        <el-menu-item index="/profile">个人中心</el-menu-item>
        <el-menu-item v-if="auth.user?.is_admin" index="/admin/users">用户管理</el-menu-item>
      </el-menu>
      <notification-bell v-if="auth.user" />
      <el-dropdown v-if="auth.user" @command="onCommand">
        <span class="user-entry">
          <el-avatar :size="30" :src="auth.user.avatar">{{ auth.user.nickname.slice(0, 1) }}</el-avatar>
          <span>{{ auth.user.nickname }}</span>
          <el-tag v-if="auth.user.is_vip" type="warning" effect="dark" size="small" round>VIP</el-tag>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile">个人中心</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </el-header>
    <el-main><router-view /></el-main>
  </el-container>`,
  setup() {
    const router = VueRouter.useRouter()
    const route = VueRouter.useRoute()
    // 详情页 /kb/1 也高亮“知识库”菜单
    const activeMenu = computed(() => (route.path.startsWith('/kb') ? '/kb' : route.path))
    const onCommand = (cmd) => {
      if (cmd === 'logout') {
        stopNotificationPolling()
        auth.logout()
        router.replace('/login')
      } else {
        router.push('/profile')
      }
    }
    return { auth, activeMenu, onCommand, HOME_PATH, permissionBadge }
  },
}

const router = VueRouter.createRouter({
  history: VueRouter.createWebHashHistory(),
  routes: [
    { path: '/login', component: LoginPage, meta: { guest: true } },
    { path: '/register', component: RegisterPage, meta: { guest: true } },
    {
      path: '/',
      component: Layout,
      redirect: HOME_PATH,
      children: [
        { path: 'kb', component: KbListPage, meta: { auth: true } },
        { path: 'kb/:kbId(\\d+)', component: KbDetailPage, meta: { auth: true } },
        // 文档路由全部嵌在知识库路径下，没有独立的 /docs 入口
        { path: 'kb/:kbId(\\d+)/docs/new', component: DocEditPage, meta: { auth: true } },
        { path: 'kb/:kbId(\\d+)/docs/:docId(\\d+)', component: DocViewPage, meta: { auth: true } },
        { path: 'kb/:kbId(\\d+)/docs/:docId(\\d+)/edit', component: DocEditPage, meta: { auth: true } },
        { path: 'permissions', component: PermissionCenterPage, meta: { auth: true } },
        { path: 'invite/:token', component: InviteLinkPage, meta: { auth: true } },
        { path: 'notifications', component: NotificationsPage, meta: { auth: true } },
        { path: 'profile', component: ProfilePage, meta: { auth: true } },
        { path: 'admin/users', component: AdminUsersPage, meta: { auth: true, admin: true } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: HOME_PATH },
  ],
})

router.beforeEach(async (to) => {
  // 有 token 但还没拉取用户信息（如刷新页面）
  if (auth.token && !auth.user) {
    try {
      auth.user = await api.me()
    } catch {
      auth.logout()
    }
  }
  if (to.meta.auth && !auth.user) return { path: '/login', query: { redirect: to.fullPath } }
  if (to.meta.guest && auth.user) return HOME_PATH
  if (to.meta.admin && !auth.user.is_admin) {
    ElementPlus.ElMessage.warning('无管理员权限')
    return HOME_PATH
  }
})

// 每次切换页面刷新“权限中心”的待处理数量（收到的邀请 + 待我审批）；登录后开始轮询通知
router.afterEach((to) => {
  if (!to.meta.auth) return
  refreshPermissionBadge()
  startNotificationPolling()
})

Vue.createApp({ template: '<router-view />' })
  .use(router)
  .use(ElementPlus, { locale: ElementPlusLocaleZhCn })
  .mount('#app')
