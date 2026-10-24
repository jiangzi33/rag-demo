const Layout = {
  template: `
  <el-container class="layout">
    <el-header class="header">
      <div class="brand" @click="$router.push('/profile')">📚 知识库</div>
      <el-menu mode="horizontal" :ellipsis="false" router :default-active="$route.path" class="nav">
        <el-menu-item index="/profile">个人中心</el-menu-item>
        <el-menu-item v-if="auth.user?.is_admin" index="/admin/users">用户管理</el-menu-item>
      </el-menu>
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
    const onCommand = (cmd) => {
      if (cmd === 'logout') {
        auth.logout()
        router.replace('/login')
      } else {
        router.push('/profile')
      }
    }
    return { auth, onCommand }
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
      redirect: '/profile',
      children: [
        { path: 'profile', component: ProfilePage, meta: { auth: true } },
        { path: 'admin/users', component: AdminUsersPage, meta: { auth: true, admin: true } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/profile' },
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
  if (to.meta.guest && auth.user) return '/profile'
  if (to.meta.admin && !auth.user.is_admin) {
    ElementPlus.ElMessage.warning('无管理员权限')
    return '/profile'
  }
})

Vue.createApp({ template: '<router-view />' })
  .use(router)
  .use(ElementPlus, { locale: ElementPlusLocaleZhCn })
  .mount('#app')
