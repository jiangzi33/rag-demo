const { ref, reactive, onMounted, computed } = Vue
const { ElMessage, ElMessageBox } = ElementPlus

const confirmRule = (form, field = 'password') => ({
  validator: (_, value, cb) => (value === form[field] ? cb() : cb(new Error('两次输入的密码不一致'))),
  trigger: 'blur',
})

// ---------------- 登录 ----------------
const LoginPage = {
  template: `
  <div class="auth-wrap">
    <el-card class="auth-card" shadow="never">
      <h2 class="auth-title">登录知识库</h2>
      <el-form ref="formRef" :model="form" :rules="formRules" size="large" @keyup.enter="submit">
        <el-form-item prop="account">
          <el-input v-model="form.account" placeholder="用户名 / 邮箱" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码" show-password />
        </el-form-item>
        <el-button type="primary" class="full" :loading="loading" @click="submit">登 录</el-button>
      </el-form>
      <div class="auth-foot">还没有账号？<router-link to="/register">立即注册</router-link></div>
    </el-card>
  </div>`,
  setup() {
    const router = VueRouter.useRouter()
    const route = VueRouter.useRoute()
    const formRef = ref()
    const loading = ref(false)
    const form = reactive({ account: '', password: '' })
    const formRules = {
      account: [{ required: true, message: '请输入用户名或邮箱', trigger: 'blur' }],
      password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
    }
    const submit = async () => {
      if (!(await formRef.value.validate().catch(() => false))) return
      loading.value = true
      try {
        auth.setLogin(await api.login(form))
        ElMessage.success('登录成功')
        router.replace(route.query.redirect || '/profile')
      } finally {
        loading.value = false
      }
    }
    return { formRef, form, formRules, loading, submit }
  },
}

// ---------------- 注册 ----------------
const RegisterPage = {
  template: `
  <div class="auth-wrap">
    <el-card class="auth-card" shadow="never">
      <h2 class="auth-title">注册账号</h2>
      <el-form ref="formRef" :model="form" :rules="formRules" size="large" label-position="top">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="字母开头，4-32 位" />
        </el-form-item>
        <el-form-item label="昵称" prop="nickname">
          <el-input v-model="form.nickname" placeholder="选填，默认同用户名" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="name@example.com" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="6-64 位，含字母和数字" />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input v-model="form.confirm" type="password" show-password />
        </el-form-item>
        <el-button type="primary" class="full" :loading="loading" @click="submit">注 册</el-button>
      </el-form>
      <div class="auth-foot">已有账号？<router-link to="/login">去登录</router-link></div>
    </el-card>
  </div>`,
  setup() {
    const router = VueRouter.useRouter()
    const formRef = ref()
    const loading = ref(false)
    const form = reactive({ username: '', nickname: '', email: '', password: '', confirm: '' })
    const formRules = {
      ...rules,
      confirm: [{ required: true, message: '请再次输入密码', trigger: 'blur' }, confirmRule(form)],
    }
    const submit = async () => {
      if (!(await formRef.value.validate().catch(() => false))) return
      loading.value = true
      try {
        const { confirm, ...payload } = form
        auth.setLogin(await api.register({ ...payload, nickname: payload.nickname || null }))
        ElMessage.success('注册成功')
        router.replace('/profile')
      } finally {
        loading.value = false
      }
    }
    return { formRef, form, formRules, loading, submit }
  },
}

// ---------------- 个人中心 ----------------
const ProfilePage = {
  template: `
  <div class="page">
    <el-row :gutter="20">
      <el-col :md="8" :xs="24">
        <el-card shadow="never" class="profile-card">
          <el-upload class="avatar-uploader" :show-file-list="false" :http-request="uploadAvatar"
                     :before-upload="beforeUpload" accept="image/png,image/jpeg,image/gif,image/webp">
            <el-avatar :size="96" :src="user.avatar">{{ user.nickname?.slice(0, 1) }}</el-avatar>
            <div class="avatar-mask">更换头像</div>
          </el-upload>
          <h3>{{ user.nickname }} <el-tag v-if="user.is_vip" type="warning" effect="dark" size="small" round>VIP</el-tag></h3>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="用户ID">{{ user.id }}</el-descriptions-item>
            <el-descriptions-item label="用户名">{{ user.username }}</el-descriptions-item>
            <el-descriptions-item label="邮箱">{{ user.email }}</el-descriptions-item>
            <el-descriptions-item label="注册时间">{{ formatTime(user.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="会员">{{ user.is_vip ? 'VIP 会员' : '普通用户' }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="USER_STATUS[user.status].type" size="small">{{ USER_STATUS[user.status].text }}</el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :md="16" :xs="24">
        <el-card shadow="never">
          <el-tabs>
            <el-tab-pane label="基本资料">
              <el-form ref="profileRef" :model="profile" :rules="profileRules" label-width="80px" class="narrow-form">
                <el-form-item label="昵称" prop="nickname"><el-input v-model="profile.nickname" /></el-form-item>
                <el-form-item label="邮箱" prop="email"><el-input v-model="profile.email" /></el-form-item>
                <el-form-item><el-button type="primary" :loading="saving" @click="saveProfile">保存</el-button></el-form-item>
              </el-form>
            </el-tab-pane>
            <el-tab-pane label="修改密码">
              <el-form ref="pwdRef" :model="pwd" :rules="pwdRules" label-width="80px" class="narrow-form">
                <el-form-item label="原密码" prop="old_password">
                  <el-input v-model="pwd.old_password" type="password" show-password />
                </el-form-item>
                <el-form-item label="新密码" prop="new_password">
                  <el-input v-model="pwd.new_password" type="password" show-password />
                </el-form-item>
                <el-form-item label="确认密码" prop="confirm">
                  <el-input v-model="pwd.confirm" type="password" show-password />
                </el-form-item>
                <el-form-item><el-button type="primary" :loading="saving" @click="savePassword">修改密码</el-button></el-form-item>
              </el-form>
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </el-col>
    </el-row>
  </div>`,
  setup() {
    const router = VueRouter.useRouter()
    const user = computed(() => auth.user)
    const saving = ref(false)
    const profileRef = ref()
    const pwdRef = ref()
    const profile = reactive({ nickname: auth.user.nickname, email: auth.user.email })
    const profileRules = {
      nickname: [{ required: true, message: '请输入昵称', trigger: 'blur' }, ...rules.nickname],
      email: rules.email,
    }
    const pwd = reactive({ old_password: '', new_password: '', confirm: '' })
    const pwdRules = {
      old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
      new_password: rules.password,
      confirm: [{ required: true, message: '请再次输入新密码', trigger: 'blur' }, confirmRule(pwd, 'new_password')],
    }

    const saveProfile = async () => {
      if (!(await profileRef.value.validate().catch(() => false))) return
      saving.value = true
      try {
        auth.user = await api.updateMe(profile)
        ElMessage.success('资料已更新')
      } finally {
        saving.value = false
      }
    }

    const savePassword = async () => {
      if (!(await pwdRef.value.validate().catch(() => false))) return
      saving.value = true
      try {
        await api.changePassword({ old_password: pwd.old_password, new_password: pwd.new_password })
        ElMessage.success('密码已修改，请重新登录')
        auth.logout()
        router.replace('/login')
      } finally {
        saving.value = false
      }
    }

    const beforeUpload = (file) => {
      if (file.size > 2 * 1024 * 1024) {
        ElMessage.error('头像不能超过 2MB')
        return false
      }
      return true
    }
    const uploadAvatar = async ({ file }) => {
      auth.user = await api.uploadAvatar(file)
      ElMessage.success('头像已更新')
    }

    return {
      user, saving, profileRef, profile, profileRules, pwdRef, pwd, pwdRules,
      saveProfile, savePassword, beforeUpload, uploadAvatar, formatTime, USER_STATUS,
    }
  },
}

// ---------------- 用户管理（管理员） ----------------
const AdminUsersPage = {
  template: `
  <div class="page">
    <el-card shadow="never">
      <el-form :inline="true" :model="query" @submit.prevent>
        <el-form-item>
          <el-input v-model="query.keyword" placeholder="用户名 / 昵称 / 邮箱" clearable @keyup.enter="search" @clear="search" />
        </el-form-item>
        <el-form-item>
          <el-select v-model="query.status" placeholder="状态" clearable style="width: 120px" @change="search">
            <el-option v-for="(s, k) in USER_STATUS" :key="k" :label="s.text" :value="Number(k)" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-select v-model="query.is_vip" placeholder="会员" clearable style="width: 120px" @change="search">
            <el-option label="VIP" :value="true" /><el-option label="普通" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" @click="search">查询</el-button></el-form-item>
      </el-form>

      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column label="用户" min-width="200">
          <template #default="{ row }">
            <div class="user-cell">
              <el-avatar :size="32" :src="row.avatar">{{ row.nickname.slice(0, 1) }}</el-avatar>
              <div><div>{{ row.nickname }}</div><div class="muted">@{{ row.username }}</div></div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="180" />
        <el-table-column label="注册时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="VIP" width="80" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.is_vip" @change="(v) => toggleVip(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="USER_STATUS[row.status].type" size="small">{{ USER_STATUS[row.status].text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <template v-if="row.id !== me.id">
              <el-button v-if="row.status !== 1" link type="primary" @click="changeStatus(row, 1)">启用</el-button>
              <el-button v-if="row.status === 1" link type="warning" @click="changeStatus(row, 0)">禁用</el-button>
              <el-button v-if="row.status !== 2" link type="danger" @click="changeStatus(row, 2)">注销</el-button>
            </template>
            <span v-else class="muted">当前账号</span>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination class="pager" background layout="total, sizes, prev, pager, next"
        :total="total" v-model:current-page="query.page" v-model:page-size="query.size"
        :page-sizes="[10, 20, 50]" @current-change="load" @size-change="search" />
    </el-card>
  </div>`,
  setup() {
    const me = computed(() => auth.user)
    const loading = ref(false)
    const list = ref([])
    const total = ref(0)
    const query = reactive({ page: 1, size: 10, keyword: '', status: null, is_vip: null })

    const load = async () => {
      loading.value = true
      try {
        const params = Object.fromEntries(Object.entries(query).filter(([, v]) => v !== '' && v !== null))
        const data = await api.adminListUsers(params)
        list.value = data.items
        total.value = data.total
      } finally {
        loading.value = false
      }
    }
    const search = () => { query.page = 1; load() }

    const replaceRow = (u) => {
      const i = list.value.findIndex((r) => r.id === u.id)
      if (i >= 0) list.value[i] = u
    }
    const toggleVip = async (row, v) => {
      replaceRow(await api.adminSetVip(row.id, v))
      ElMessage.success(v ? '已设为 VIP' : '已取消 VIP')
    }
    const STATUS_ACTION = { 0: '禁用', 1: '启用', 2: '注销' }
    const changeStatus = async (row, status) => {
      const ok = await ElMessageBox.confirm(
        `确定要${STATUS_ACTION[status]}用户「${row.nickname}」吗？`, '提示', { type: 'warning' },
      ).catch(() => false)
      if (!ok) return
      replaceRow(await api.adminSetStatus(row.id, status))
      ElMessage.success('操作成功')
    }

    onMounted(load)
    return { me, loading, list, total, query, load, search, toggleVip, changeStatus, formatTime, USER_STATUS }
  },
}
