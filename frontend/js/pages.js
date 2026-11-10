const { ref, reactive, onMounted, computed } = Vue
const { ElMessage, ElMessageBox } = ElementPlus

// 登录 / 注册后的默认落地页
const HOME_PATH = '/kb'

const confirmRule = (form, field = 'password') => ({
  validator: (_, value, cb) => (value === form[field] ? cb() : cb(new Error('两次输入的密码不一致'))),
  trigger: 'blur',
})

// 登录 / 注册后回到原页面：只允许知识库页面和邀请链接（未登录打开邀请链接时先登录 / 注册），其余一律进入知识库主页
const safeRedirect = (redirect) =>
  typeof redirect === 'string' && (redirect.startsWith('/kb') || redirect.startsWith('/invite/')) ? redirect : HOME_PATH

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
      <div class="auth-foot">还没有账号？<router-link :to="{ path: '/register', query: $route.query }">立即注册</router-link></div>
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
        router.replace(safeRedirect(route.query.redirect))
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
      <div class="auth-foot">已有账号？<router-link :to="{ path: '/login', query: $route.query }">去登录</router-link></div>
    </el-card>
  </div>`,
  setup() {
    const router = VueRouter.useRouter()
    const route = VueRouter.useRoute()
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
        router.replace(safeRedirect(route.query.redirect))
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

// ---------------- 知识库：创建 / 编辑弹窗 ----------------
// 传入 kb 时为编辑，否则为创建；保存成功后触发 saved(知识库)
const KbFormDialog = {
  props: { modelValue: Boolean, kb: Object },
  emits: ['update:modelValue', 'saved'],
  template: `
  <el-dialog :model-value="modelValue" :title="kb ? '编辑知识库' : '新建知识库'" width="440px"
             @update:model-value="$emit('update:modelValue', $event)" @open="onOpen" @opened="focus">
    <el-form ref="formRef" :model="form" :rules="formRules" label-position="top" @submit.prevent="submit">
      <el-form-item label="知识库名称" prop="name">
        <el-input ref="inputRef" v-model="form.name" maxlength="64" show-word-limit placeholder="例如：产品文档" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">{{ kb ? '保存' : '创建' }}</el-button>
    </template>
  </el-dialog>`,
  setup(props, { emit }) {
    const formRef = ref()
    const inputRef = ref()
    const saving = ref(false)
    const form = reactive({ name: '' })
    const formRules = { name: rules.kbName }

    const onOpen = () => {
      form.name = props.kb?.name || ''
      formRef.value?.clearValidate()
    }
    const focus = () => inputRef.value?.focus()
    const submit = async () => {
      if (!(await formRef.value.validate().catch(() => false))) return
      saving.value = true
      try {
        const data = { name: form.name.trim() }
        const kb = props.kb ? await api.updateKb(props.kb.id, data) : await api.createKb(data)
        ElMessage.success(props.kb ? '已保存' : '创建成功')
        emit('update:modelValue', false)
        emit('saved', kb)
      } finally {
        saving.value = false
      }
    }
    return { formRef, inputRef, saving, form, formRules, onOpen, focus, submit }
  },
}

const confirmDeleteKb = async (kb) => {
  const ok = await ElMessageBox.confirm(
    `知识库及其中的所有文档都将被删除且不可恢复，确定要删除「${kb.name}」吗？`, '删除知识库',
    { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' },
  ).catch(() => false)
  if (!ok) return false
  await api.deleteKb(kb.id)
  ElMessage.success('已删除')
  return true
}

// ---------------- 知识库主页 ----------------
const KbListPage = {
  components: { KbFormDialog, PermTag, PendingTag },
  template: `
  <div class="page">
    <div class="kb-toolbar">
      <h2 class="page-title">知识库</h2>
      <el-radio-group v-model="query.scope" @change="search">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="mine">我有权限的</el-radio-button>
      </el-radio-group>
      <el-input v-model="query.keyword" placeholder="搜索知识库名称" clearable class="kb-search"
                @keyup.enter="search" @clear="search" />
      <el-button type="primary" @click="openCreate">+ 新建知识库</el-button>
    </div>

    <div v-loading="loading" class="kb-body">
      <el-empty v-if="!loading && !list.length"
                :description="query.keyword ? '没有匹配的知识库' : query.scope === 'mine' ? '还没有你有权限的知识库' : '还没有知识库'">
        <el-button v-if="!query.keyword" type="primary" @click="openCreate">创建第一个知识库</el-button>
      </el-empty>
      <el-row v-else :gutter="16">
        <el-col v-for="kb in list" :key="kb.id" :xs="24" :sm="12" :lg="8">
          <el-card shadow="hover" class="kb-card" @click="$router.push('/kb/' + kb.id)">
            <div class="kb-card-head">
              <div class="kb-icon">{{ kb.name.slice(0, 1) }}</div>
              <div class="kb-name" :title="kb.name">{{ kb.name }}</div>
              <span @click.stop>
              <el-dropdown trigger="click" @command="(cmd) => onCommand(cmd, kb)">
                <el-button link class="kb-more">···</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="detail">查看详情</el-dropdown-item>
                    <el-dropdown-item v-if="hasPerm(kb.my_permission, 'manage')" command="edit">编辑</el-dropdown-item>
                    <el-dropdown-item v-if="hasPerm(kb.my_permission, 'owner')" command="delete" divided class="danger-item">删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              </span>
            </div>
            <div class="muted kb-meta">
              <perm-tag :perm="kb.my_permission" partial="未授权" />
              <pending-tag :perm="kb.pending_permission" />
              <span>{{ kb.my_permission === 'owner' ? '' : '管理员：' + (kb.owner?.nickname || '-') + ' · ' }}更新于 {{ formatTime(kb.updated_at) }}</span>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <el-pagination v-if="total > query.size" class="pager" background layout="total, prev, pager, next"
      :total="total" v-model:current-page="query.page" :page-size="query.size" @current-change="load" />

    <kb-form-dialog v-model="dialog.visible" :kb="dialog.kb" @saved="load" />
  </div>`,
  setup() {
    const router = VueRouter.useRouter()
    const loading = ref(false)
    const list = ref([])
    const total = ref(0)
    const query = reactive({ page: 1, size: 12, keyword: '', scope: 'all' })
    const dialog = reactive({ visible: false, kb: null })

    const load = async () => {
      loading.value = true
      try {
        const params = { page: query.page, size: query.size, keyword: query.keyword || undefined, scope: query.scope }
        const data = await api.listKbs(params)
        // 删除最后一页的最后一条后回退一页
        if (!data.items.length && query.page > 1) {
          query.page -= 1
          return load()
        }
        list.value = data.items
        total.value = data.total
      } finally {
        loading.value = false
      }
    }
    const search = () => { query.page = 1; load() }

    const openCreate = () => Object.assign(dialog, { visible: true, kb: null })
    const onCommand = async (cmd, kb) => {
      if (cmd === 'detail') router.push('/kb/' + kb.id)
      else if (cmd === 'edit') Object.assign(dialog, { visible: true, kb })
      else if (cmd === 'delete' && (await confirmDeleteKb(kb))) load()
    }

    onMounted(load)
    return { loading, list, total, query, dialog, load, search, openCreate, onCommand, formatTime, hasPerm }
  },
}

// ---------------- 知识库详情（含文档列表） ----------------
// 文档只能在知识库内创建和浏览，没有独立的文档入口
const KbDetailPage = {
  components: { KbFormDialog, PermTag, PendingTag, PermissionDrawer, ApplyPermissionDialog, NoAccess },
  template: `
  <div class="page">
    <el-page-header @back="$router.push('/kb')" class="kb-header">
      <template #content><span class="page-title">{{ kb?.name || '知识库详情' }}</span></template>
      <template #extra v-if="kb">
        <div class="header-actions">
          <el-button type="primary" plain @click="$router.push({ path: '/search', query: { kb: kb.id } })">在此知识库中检索</el-button>
          <el-button v-if="can('manage')" @click="openMembers('kb', kb.id, kb.name)">成员与权限</el-button>
          <el-button v-else-if="kb.pending_permission" disabled>{{ PERMISSION_TYPE_LABEL[kb.pending_permission] }}权限审批中</el-button>
          <el-button v-else @click="applying = true">申请{{ kb.my_permission ? '更高' : '' }}权限</el-button>
          <el-button v-if="can('manage')" @click="editing = true">编辑知识库</el-button>
          <el-button v-if="can('owner')" type="danger" plain @click="remove">删除知识库</el-button>
        </div>
      </template>
    </el-page-header>

    <el-card shadow="never" v-loading="loading" class="kb-info">
      <el-descriptions v-if="kb" :column="isNarrow ? 1 : 2" border size="small">
        <el-descriptions-item label="管理员">
          <div class="user-cell" v-if="kb.owner">
            <el-avatar :size="22" :src="kb.owner.avatar">{{ kb.owner.nickname.slice(0, 1) }}</el-avatar>
            <span>{{ kb.owner.nickname }}</span><span class="muted">@{{ kb.owner.username }}</span>
          </div>
          <span v-else class="muted">用户已删除</span>
        </el-descriptions-item>
        <el-descriptions-item label="我的权限">
          <perm-tag :perm="kb.my_permission" partial="未授权" />
          <pending-tag :perm="kb.pending_permission" style="margin-left: 6px" />
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatTime(kb.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="更新时间">{{ formatTime(kb.updated_at) }}</el-descriptions-item>
      </el-descriptions>
      <no-access v-else-if="!loading" target-type="kb" :target-id="kbId" />
      <p v-if="kb && !can('write')" class="muted perm-hint kb-perm-hint">
        所有文档对你可见：有读权限才能打开，有写权限才能编辑，有管理权限才能删除。可以对整个知识库申请权限，也可以在下方针对单篇文档申请。
      </p>
    </el-card>

    <el-card v-if="kb" shadow="never">
      <div class="kb-toolbar">
        <h3 class="section-title">文档 <span class="muted">{{ docTotal }}</span></h3>
        <el-input v-model="docQuery.keyword" placeholder="搜索文档标题" clearable class="kb-search"
                  @keyup.enter="searchDocs" @clear="searchDocs" />
        <el-button v-if="can('write')" type="primary" @click="$router.push(docBase + '/new')">+ 新建文档</el-button>
      </div>

      <el-empty v-if="!docLoading && !docs.length" :description="docQuery.keyword ? '没有匹配的文档' : '这个知识库还没有文档'">
        <el-button v-if="!docQuery.keyword && can('write')" type="primary" @click="$router.push(docBase + '/new')">写第一篇文档</el-button>
      </el-empty>
      <el-table v-else :data="docs" v-loading="docLoading" @row-click="openDoc" row-class-name="clickable-row">
        <el-table-column label="标题" min-width="220">
          <template #default="{ row }">
            <span class="doc-title-cell" :class="{ locked: !hasPerm(row.my_permission, 'read') }">
              <span v-if="!hasPerm(row.my_permission, 'read')" class="lock-icon" title="没有读权限">🔒</span>{{ row.title }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="作者" width="140">
          <template #default="{ row }">{{ row.author?.nickname || '-' }}</template>
        </el-table-column>
        <el-table-column label="最近编辑" width="170">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="发布日期" width="170">
          <template #default="{ row }">{{ formatTime(row.published_at) }}</template>
        </el-table-column>
        <el-table-column label="我的权限" width="130">
          <template #default="{ row }">
            <div class="perm-cell">
              <perm-tag :perm="row.my_permission" partial="无权限" />
              <pending-tag :perm="row.pending_permission" />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <span @click.stop>
              <el-button v-if="hasPerm(row.my_permission, 'read')" link type="primary"
                         @click="$router.push(docBase + '/' + row.id)">查看</el-button>
              <el-button v-if="hasPerm(row.my_permission, 'write')" link type="primary"
                         @click="$router.push(docBase + '/' + row.id + '/edit')">编辑</el-button>
              <el-button v-if="hasPerm(row.my_permission, 'manage')" link type="primary"
                         @click="openMembers('doc', row.id, row.title)">权限</el-button>
              <el-button v-if="canDeleteDoc(row)" link type="danger" @click="removeDoc(row)">删除</el-button>
              <el-button v-if="!hasPerm(row.my_permission, 'manage') && !row.pending_permission" link type="warning"
                         @click="openApply(row)">申请{{ row.my_permission ? '更高' : '' }}权限</el-button>
            </span>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination v-if="docTotal > docQuery.size" class="pager" background layout="total, prev, pager, next"
        :total="docTotal" v-model:current-page="docQuery.page" :page-size="docQuery.size" @current-change="loadDocs" />
    </el-card>

    <kb-form-dialog v-model="editing" :kb="kb" @saved="(v) => (kb = v)" />
    <permission-drawer v-model="members.visible" :target-type="members.targetType" :target-id="members.targetId"
                       :target-name="members.targetName" />
    <apply-permission-dialog v-if="kb" v-model="applying" target-type="kb" :target-id="kb.id" :target-name="kb.name"
                             :current="kb.my_permission" @applied="load" />
    <apply-permission-dialog v-model="docApply.visible" target-type="doc" :target-id="docApply.targetId"
                             :target-name="docApply.targetName" :current="docApply.current" @applied="loadDocs" />
  </div>`,
  setup() {
    const route = VueRouter.useRoute()
    const router = VueRouter.useRouter()
    const kbId = route.params.kbId
    const docBase = `/kb/${kbId}/docs`
    const kb = ref(null)
    const loading = ref(false)
    const editing = ref(false)
    const isNarrow = window.innerWidth < 768

    const docs = ref([])
    const docTotal = ref(0)
    const docLoading = ref(false)
    const docQuery = reactive({ page: 1, size: 20, keyword: '' })

    const load = async () => {
      loading.value = true
      try {
        kb.value = await api.getKb(kbId)
      } catch {
        kb.value = null
        return
      } finally {
        loading.value = false
      }
      loadDocs()
    }
    const loadDocs = async () => {
      docLoading.value = true
      try {
        const params = { page: docQuery.page, size: docQuery.size, keyword: docQuery.keyword || undefined }
        const data = await api.listDocs(kbId, params)
        if (!data.items.length && docQuery.page > 1) {
          docQuery.page -= 1
          return loadDocs()
        }
        docs.value = data.items
        docTotal.value = data.total
      } finally {
        docLoading.value = false
      }
    }
    const searchDocs = () => { docQuery.page = 1; loadDocs() }

    const remove = async () => {
      if (await confirmDeleteKb(kb.value)) router.replace('/kb')
    }
    const removeDoc = async (doc) => {
      if (await confirmDeleteDoc(kbId, doc)) loadDocs()
    }

    // 权限：按 my_permission 显示操作按钮（后端同样会校验）
    const can = (need) => hasPerm(kb.value?.my_permission, need)
    const applying = ref(false)
    const members = reactive({ visible: false, targetType: 'kb', targetId: 0, targetName: '' })
    const openMembers = (targetType, targetId, targetName) =>
      Object.assign(members, { visible: true, targetType, targetId, targetName })
    // 针对单篇文档申请权限；没有读权限的文档点击时直接弹出申请
    const docApply = reactive({ visible: false, targetId: 0, targetName: '', current: null })
    const openApply = (doc) =>
      Object.assign(docApply, { visible: true, targetId: doc.id, targetName: doc.title, current: doc.my_permission })
    const openDoc = (doc) => {
      if (hasPerm(doc.my_permission, 'read')) router.push(`${docBase}/${doc.id}`)
      else if (doc.pending_permission) ElMessage.info('权限申请审批中，管理者同意后即可查看')
      else openApply(doc)
    }

    onMounted(load)
    return {
      kb, kbId, loading, editing, isNarrow, docBase, docs, docTotal, docLoading, docQuery, applying, members, docApply,
      loadDocs, searchDocs, remove, removeDoc, can, openMembers, openApply, openDoc, hasPerm, canDeleteDoc, formatTime,
      PERMISSION_TYPE_LABEL,
    }
  },
}
