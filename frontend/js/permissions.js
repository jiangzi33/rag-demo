// 权限管理：成员与权限抽屉、申请权限弹窗、无权访问提示、权限中心页面。
// 本文件在 pages.js 之前加载（页面组件在定义时就会引用这里的组件），
// 因此 ref / ElMessage 等只能在函数运行时使用，不能在顶层直接调用。

// 顶部导航的待处理数量：收到的邀请 + 待我审批的申请
const permissionBadge = Vue.reactive({ count: 0 })
const refreshPermissionBadge = async () => {
  if (!auth.user) return
  try {
    const [mine, approvals] = await Promise.all([api.myPermissions({ status: 'pending' }), api.pendingApprovals()])
    permissionBadge.count = mine.filter((p) => p.direction === 'invite').length + approvals.length
  } catch {
    // 角标失败不影响页面
  }
}

// 跳转到授权对象：知识库 / 文档（对象已删除时为 null）
const targetLink = (p) => {
  if (!p.kb_id) return null
  return p.target_type === 'kb' ? `/kb/${p.kb_id}` : `/kb/${p.kb_id}/docs/${p.target_id}`
}

// 用户头像 + 昵称
const UserCell = {
  props: { user: Object, size: { type: Number, default: 24 } },
  template: `
  <div class="user-cell" v-if="user">
    <el-avatar :size="size" :src="user.avatar">{{ user.nickname.slice(0, 1) }}</el-avatar>
    <div class="user-cell-text"><span>{{ user.nickname }}</span><span class="muted">@{{ user.username }}</span></div>
  </div>
  <span v-else class="muted">用户已删除</span>`,
}

// 当前用户权限标签
const PermTag = {
  props: { perm: String, partial: String },
  template: `
  <el-tag v-if="perm" :type="PERM_TAG[perm]" size="small" effect="light">{{ PERM_LABEL[perm] }}</el-tag>
  <el-tag v-else-if="partial" type="info" size="small" effect="plain">{{ partial }}</el-tag>`,
  setup: () => ({ PERM_TAG, PERM_LABEL }),
}

// 待审批的追加申请：审批期间原权限照常生效
const PendingTag = {
  props: { perm: String },
  template: `<el-tag v-if="perm" type="warning" size="small" effect="plain">{{ PERMISSION_TYPE_LABEL[perm] }}权限审批中</el-tag>`,
  setup: () => ({ PERMISSION_TYPE_LABEL }),
}

// ---------------- 申请权限弹窗 ----------------
// current 为当前已有的权限，只能申请比它更高的权限；审批期间保留当前权限，通过后替换为新权限
const ApplyPermissionDialog = {
  props: { modelValue: Boolean, targetType: String, targetId: [Number, String], targetName: String, current: String },
  emits: ['update:modelValue', 'applied'],
  template: `
  <el-dialog :model-value="modelValue" title="申请权限" width="460px" @update:model-value="$emit('update:modelValue', $event)" @open="onOpen">
    <p class="apply-target">
      {{ TARGET_LABEL[targetType] }}：<b>{{ targetName || ('#' + targetId) }}</b>
      <perm-tag v-if="current" :perm="current" style="margin-left: 8px" />
    </p>
    <el-radio-group v-model="type" class="perm-type-group">
      <el-radio v-for="t in options" :key="t.value" :value="t.value" border>
        <b>{{ t.label }}</b><span class="muted">{{ t.desc }}</span>
      </el-radio>
    </el-radio-group>
    <p class="muted">
      提交后需要{{ TARGET_LABEL[targetType] }}的管理者同意才会生效，可在「权限中心 · 我的申请」查看进度。
      <template v-if="current">审批期间你仍保留当前的{{ PERM_LABEL[current] }}权限。</template>
    </p>
    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!type" @click="submit">提交申请</el-button>
    </template>
  </el-dialog>`,
  components: { PermTag },
  setup(props, { emit }) {
    const type = Vue.ref('')
    const saving = Vue.ref(false)
    const options = Vue.computed(() => PERMISSION_TYPES.filter((t) => !hasPerm(props.current, t.value)))
    const onOpen = () => { type.value = options.value[0]?.value || '' }
    const submit = async () => {
      saving.value = true
      try {
        await api.applyPermission({ target_type: props.targetType, target_id: Number(props.targetId), permission_type: type.value })
        ElMessage.success('申请已提交，等待管理者审批')
        emit('update:modelValue', false)
        emit('applied')
      } finally {
        saving.value = false
      }
    }
    return { type, saving, options, onOpen, submit, TARGET_LABEL, PERM_LABEL }
  },
}

// ---------------- 无权访问 / 不存在提示 ----------------
// 后端：对象不存在返回 404；存在但权限不足返回 403（forbidden），此时提供申请入口。
// need 为缺少的权限：read（打开文档）/ write（编辑、新建）；current 为当前已有的权限
const NoAccess = {
  components: { ApplyPermissionDialog },
  props: {
    targetType: String, targetId: [Number, String], targetName: String, backTo: { type: String, default: '/kb' },
    forbidden: Boolean, need: { type: String, default: 'read' }, current: String,
  },
  template: `
  <el-result v-if="forbidden" icon="warning" :title="'没有' + NEED_TEXT[need] + '权限'" :sub-title="subTitle">
    <template #extra>
      <el-button @click="$router.push(backTo)">返回</el-button>
      <el-button type="primary" @click="applying = true">申请权限</el-button>
    </template>
  </el-result>
  <el-result v-else icon="info" :title="TARGET_LABEL[targetType] + '不存在'" sub-title="可能已被删除，或链接有误">
    <template #extra><el-button @click="$router.push(backTo)">返回</el-button></template>
  </el-result>
  <apply-permission-dialog v-model="applying" :target-type="targetType" :target-id="targetId" :target-name="targetName"
                           :current="current" />`,
  setup(props) {
    const NEED_TEXT = { read: '阅读', write: '编辑' }
    const applying = Vue.ref(false)
    const subTitle = Vue.computed(() => {
      const target = TARGET_LABEL[props.targetType]
      const have = props.current ? `你当前对该${target}只有${PERM_LABEL[props.current]}权限` : `你还没有该${target}的${props.need === 'write' ? '写' : '读'}权限`
      return `${have}，可以申请权限，管理者同意后即可${NEED_TEXT[props.need]}`
    })
    return { applying, subTitle, NEED_TEXT, TARGET_LABEL }
  },
}

// ---------------- 邀请链接（成员与权限抽屉中使用） ----------------
// 打开链接即直接获得权限并跳转到目标对象；生成人失去管理权限后链接自动失效
const inviteLinkUrl = (token) => `${location.origin}${location.pathname}#/invite/${token}`

// 非 HTTPS 环境下 navigator.clipboard 不可用，退回为弹窗手动复制
const copyText = async (text) => {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('链接已复制')
  } catch {
    ElMessageBox.alert(text, '请手动复制链接', { confirmButtonText: '好的' })
  }
}

const LINK_EXPIRES = [
  { value: 1, label: '1 天' },
  { value: 7, label: '7 天' },
  { value: 30, label: '30 天' },
  { value: 0, label: '永久有效' },
]

const InviteLinkPanel = {
  props: { targetType: String, targetId: [Number, String] },
  template: `
  <div class="invite-link-panel">
    <h4 class="sub-title">邀请链接</h4>
    <p class="muted perm-hint">把链接发给对方，对方登录后打开即可直接获得权限并进入该{{ TARGET_LABEL[targetType] }}，无需再接受邀请。</p>
    <el-form :inline="true" class="invite-form" @submit.prevent="create">
      <el-form-item>
        <el-select v-model="form.permission_type" style="width: 110px">
          <el-option v-for="t in PERMISSION_TYPES" :key="t.value" :label="t.label + '权限'" :value="t.value" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="form.expires_in_days" style="width: 110px">
          <el-option v-for="e in LINK_EXPIRES" :key="e.value" :label="e.label" :value="e.value" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-input-number v-model="form.max_uses" :min="1" :max="10000" placeholder="不限次数" controls-position="right" style="width: 130px" />
      </el-form-item>
      <el-form-item><el-button type="primary" :loading="creating" @click="create">生成链接</el-button></el-form-item>
    </el-form>

    <el-table :data="links" v-loading="loading" empty-text="还没有邀请链接" size="small">
      <el-table-column label="权限" width="70">
        <template #default="{ row }">{{ PERMISSION_TYPE_LABEL[row.permission_type] }}</template>
      </el-table-column>
      <el-table-column label="有效期至" min-width="150">
        <template #default="{ row }"><span class="muted">{{ row.expires_at ? formatTime(row.expires_at) : '永久有效' }}</span></template>
      </el-table-column>
      <el-table-column label="已使用" width="90">
        <template #default="{ row }">{{ row.use_count }}{{ row.max_uses ? ' / ' + row.max_uses : '' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.valid ? 'success' : 'info'" size="small">{{ row.valid ? '有效' : '已失效' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.valid" link type="primary" @click="copy(row)">复制</el-button>
          <el-button link type="danger" @click="remove(row)">{{ row.valid ? '撤销' : '删除' }}</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>`,
  setup(props) {
    const links = Vue.ref([])
    const loading = Vue.ref(false)
    const creating = Vue.ref(false)
    const form = Vue.reactive({ permission_type: 'read', expires_in_days: 7, max_uses: undefined })

    const load = async () => {
      loading.value = true
      try {
        links.value = await api.listInviteLinks(props.targetType, props.targetId)
      } finally {
        loading.value = false
      }
    }
    const create = async () => {
      creating.value = true
      try {
        const link = await api.createInviteLink({
          target_type: props.targetType, target_id: Number(props.targetId), permission_type: form.permission_type,
          expires_in_days: form.expires_in_days || null, max_uses: form.max_uses || null,
        })
        await copyText(inviteLinkUrl(link.token))
        load()
      } finally {
        creating.value = false
      }
    }
    const copy = (row) => copyText(inviteLinkUrl(row.token))
    const remove = async (row) => {
      const action = row.valid ? '撤销该链接？撤销后链接立即失效，已通过链接获得的权限不受影响' : '删除这条已失效的链接？'
      const ok = await ElMessageBox.confirm(`确定要${action}`, '提示', { type: 'warning' }).catch(() => false)
      if (!ok) return
      await api.removeInviteLink(row.id)
      ElMessage.success('已完成')
      load()
    }

    Vue.onMounted(load)
    return {
      links, loading, creating, form, create, copy, remove, formatTime,
      PERMISSION_TYPES, PERMISSION_TYPE_LABEL, TARGET_LABEL, LINK_EXPIRES,
    }
  },
}

// ---------------- 成员与权限抽屉（知识库 / 文档共用） ----------------
const PermissionDrawer = {
  components: { UserCell, InviteLinkPanel },
  props: { modelValue: Boolean, targetType: String, targetId: [Number, String], targetName: String },
  emits: ['update:modelValue'],
  template: `
  <el-drawer :model-value="modelValue" :title="'成员与权限 · ' + (targetName || '')" size="min(720px, 100%)"
             @update:model-value="$emit('update:modelValue', $event)" @open="load">
    <p class="muted perm-hint" v-if="targetType === 'kb'">知识库授权对其中所有文档生效；拥有者默认拥有全部权限，不在列表中。</p>
    <p class="muted perm-hint" v-else>文档授权只对这一篇文档生效；知识库的成员按知识库权限访问。</p>

    <el-form :inline="true" class="invite-form" @submit.prevent="invite">
      <el-form-item>
        <el-input v-model="form.username" placeholder="输入用户名邀请" clearable style="width: 200px" />
      </el-form-item>
      <el-form-item>
        <el-select v-model="form.permission_type" style="width: 110px">
          <el-option v-for="t in PERMISSION_TYPES" :key="t.value" :label="t.label + '权限'" :value="t.value" />
        </el-select>
      </el-form-item>
      <el-form-item><el-button type="primary" :loading="inviting" @click="invite">邀请</el-button></el-form-item>
    </el-form>

    <el-table :data="list" v-loading="loading" empty-text="还没有授权记录">
      <el-table-column label="成员" min-width="170">
        <template #default="{ row }"><user-cell :user="row.user" /></template>
      </el-table-column>
      <el-table-column label="权限" width="120">
        <template #default="{ row }">
          <el-select v-if="row.status !== 'reject' && row.user_id !== me.id" :model-value="row.permission_type" size="small"
                     @change="(v) => changeType(row, v)">
            <el-option v-for="t in PERMISSION_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
          <span v-else>{{ PERMISSION_TYPE_LABEL[row.permission_type] }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="130">
        <template #default="{ row }">
          <el-tag :type="PERMISSION_STATUS[row.status].type" size="small">{{ PERMISSION_STATUS[row.status].text }}</el-tag>
          <span class="muted dir-label">{{ row.direction === 'invite' ? '邀请' : '申请' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="165">
        <template #default="{ row }">
          <div class="muted time-cell">{{ row.granted_at ? '授权于 ' + formatTime(row.granted_at) : '发起于 ' + formatTime(row.created_at) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'pending' && row.direction === 'apply'">
            <el-button link type="primary" @click="decide(row, true)">同意</el-button>
            <el-button link type="danger" @click="decide(row, false)">驳回</el-button>
          </template>
          <el-button v-else-if="row.status === 'pending'" link type="warning" @click="remove(row, '撤回对 ' + who(row) + ' 的邀请')">撤回</el-button>
          <el-button v-else-if="row.status === 'accept'" link type="danger" @click="remove(row, '移除 ' + who(row) + ' 的权限')">移除</el-button>
          <el-button v-else link @click="remove(row, '删除这条已拒绝的记录')">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 每次打开抽屉重新挂载，刷新链接列表 -->
    <invite-link-panel v-if="modelValue" :key="targetType + targetId" :target-type="targetType" :target-id="targetId" />
  </el-drawer>`,
  setup(props) {
    const me = Vue.computed(() => auth.user)
    const list = Vue.ref([])
    const loading = Vue.ref(false)
    const inviting = Vue.ref(false)
    const form = Vue.reactive({ username: '', permission_type: 'read' })
    const who = (row) => row.user?.nickname || '该用户'

    const load = async () => {
      loading.value = true
      try {
        list.value = await api.targetPermissions(props.targetType, props.targetId)
      } finally {
        loading.value = false
      }
    }
    const invite = async () => {
      const username = form.username.trim()
      if (!username) return ElMessage.warning('请输入用户名')
      inviting.value = true
      try {
        await api.invitePermission({
          username, target_type: props.targetType, target_id: Number(props.targetId), permission_type: form.permission_type,
        })
        ElMessage.success('邀请已发送，对方接受后生效')
        form.username = ''
        load()
      } finally {
        inviting.value = false
      }
    }
    const changeType = async (row, v) => {
      await api.changePermissionType(row.id, v)
      ElMessage.success('权限已修改')
      load()
    }
    const decide = async (row, ok) => {
      await (ok ? api.acceptPermission(row.id) : api.rejectPermission(row.id))
      ElMessage.success(ok ? '已同意' : '已驳回')
      load()
      refreshPermissionBadge()
    }
    const remove = async (row, action) => {
      const ok = await ElMessageBox.confirm(`确定要${action}吗？`, '提示', { type: 'warning' }).catch(() => false)
      if (!ok) return
      await api.removePermission(row.id)
      ElMessage.success('已完成')
      load()
    }
    return {
      me, list, loading, inviting, form, who, load, invite, changeType, decide, remove, formatTime,
      PERMISSION_TYPES, PERMISSION_TYPE_LABEL, PERMISSION_STATUS,
    }
  },
}

// ---------------- 权限中心 ----------------
const PermissionCenterPage = {
  components: { UserCell, ApplyPermissionDialog },
  template: `
  <div class="page perm-page">
    <h2 class="page-title perm-page-title">权限中心</h2>
    <el-card shadow="never">
      <el-tabs v-model="tab" @tab-change="load">
        <el-tab-pane name="todo">
          <template #label>待处理 <el-badge v-if="todoCount" :value="todoCount" class="tab-badge" /></template>
          <div v-loading="loading">
            <h4 class="sub-title">收到的邀请</h4>
            <el-empty v-if="!invitations.length" description="没有待处理的邀请" :image-size="60" />
            <div v-for="p in invitations" :key="p.id" class="perm-item">
              <user-cell :user="p.initiator" />
              <div class="perm-item-main">
                邀请你获得 {{ TARGET_LABEL[p.target_type] }}
                <b>{{ p.target_name || '（已删除）' }}</b> 的 <el-tag size="small">{{ PERMISSION_TYPE_LABEL[p.permission_type] }}</el-tag> 权限
                <div class="muted">{{ formatTime(p.created_at) }}</div>
              </div>
              <div class="perm-item-actions">
                <el-button size="small" @click="decide(p, false)">拒绝</el-button>
                <el-button size="small" type="primary" @click="decide(p, true)">接受</el-button>
              </div>
            </div>

            <h4 class="sub-title">待我审批</h4>
            <el-empty v-if="!approvals.length" description="没有待审批的申请" :image-size="60" />
            <div v-for="p in approvals" :key="p.id" class="perm-item">
              <user-cell :user="p.user" />
              <div class="perm-item-main">
                申请 {{ TARGET_LABEL[p.target_type] }}
                <router-link v-if="targetLink(p)" :to="targetLink(p)"><b>{{ p.target_name }}</b></router-link>
                <b v-else>（已删除）</b>
                的 <el-tag size="small">{{ PERMISSION_TYPE_LABEL[p.permission_type] }}</el-tag> 权限
                <div class="muted">{{ formatTime(p.created_at) }}</div>
              </div>
              <div class="perm-item-actions">
                <el-button size="small" @click="decide(p, false)">驳回</el-button>
                <el-button size="small" type="primary" @click="decide(p, true)">同意</el-button>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="我的权限" name="granted">
          <el-table :data="granted" v-loading="loading" empty-text="还没有被授予任何权限">
            <el-table-column label="对象" min-width="220">
              <template #default="{ row }">
                <el-tag size="small" type="info" effect="plain">{{ TARGET_LABEL[row.target_type] }}</el-tag>
                <router-link v-if="targetLink(row)" :to="targetLink(row)" class="target-link">{{ row.target_name }}</router-link>
                <span v-else class="muted target-link">已删除</span>
              </template>
            </el-table-column>
            <el-table-column label="权限" width="80">
              <template #default="{ row }">{{ PERMISSION_TYPE_LABEL[row.permission_type] }}</template>
            </el-table-column>
            <el-table-column label="授权人" width="150">
              <template #default="{ row }">{{ row.initiator?.nickname || '-' }}</template>
            </el-table-column>
            <el-table-column label="授权时间" width="170">
              <template #default="{ row }">{{ formatTime(row.granted_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="90">
              <template #default="{ row }">
                <el-button link type="danger" @click="remove(row, '放弃该权限')">放弃</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="我的申请" name="applied">
          <el-table :data="applied" v-loading="loading" empty-text="还没有提交过申请">
            <el-table-column label="对象" min-width="220">
              <template #default="{ row }">
                <el-tag size="small" type="info" effect="plain">{{ TARGET_LABEL[row.target_type] }}</el-tag>
                <span class="target-link">{{ row.target_name || ('#' + row.target_id) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="申请权限" width="90">
              <template #default="{ row }">{{ PERMISSION_TYPE_LABEL[row.permission_type] }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="PERMISSION_STATUS[row.status].type" size="small">{{ PERMISSION_STATUS[row.status].text }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="申请时间" width="170">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button v-if="row.status === 'pending'" link type="warning" @click="remove(row, '撤回该申请')">撤回</el-button>
                <template v-else>
                  <el-button link type="primary" @click="reapply(row)">重新申请</el-button>
                  <el-button link @click="remove(row, '删除这条记录')">删除</el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <apply-permission-dialog v-model="applyDialog.visible" :target-type="applyDialog.targetType" :target-id="applyDialog.targetId"
                             :target-name="applyDialog.targetName" @applied="load" />
  </div>`,
  setup() {
    const route = VueRouter.useRoute()
    const tab = Vue.ref(['todo', 'granted', 'applied'].includes(route.query.tab) ? route.query.tab : 'todo')
    const loading = Vue.ref(false)
    const mine = Vue.ref([])
    const approvals = Vue.ref([])
    const applyDialog = Vue.reactive({ visible: false, targetType: 'kb', targetId: 0, targetName: '' })

    // 已接受的申请也算“我的权限”，“我的申请”只看待处理和被拒绝的
    const invitations = Vue.computed(() => mine.value.filter((p) => p.status === 'pending' && p.direction === 'invite'))
    const granted = Vue.computed(() => mine.value.filter((p) => p.status === 'accept'))
    const applied = Vue.computed(() => mine.value.filter((p) => p.direction === 'apply' && p.status !== 'accept'))
    const todoCount = Vue.computed(() => invitations.value.length + approvals.value.length)

    const load = async () => {
      loading.value = true
      try {
        const [m, a] = await Promise.all([api.myPermissions(), api.pendingApprovals()])
        mine.value = m
        approvals.value = a
        permissionBadge.count = todoCount.value
      } finally {
        loading.value = false
      }
    }
    const decide = async (p, ok) => {
      await (ok ? api.acceptPermission(p.id) : api.rejectPermission(p.id))
      ElMessage.success(ok ? (p.direction === 'invite' ? '已接受邀请' : '已同意申请') : '已拒绝')
      load()
    }
    const remove = async (p, action) => {
      const ok = await ElMessageBox.confirm(`确定要${action}吗？`, '提示', { type: 'warning' }).catch(() => false)
      if (!ok) return
      await api.removePermission(p.id)
      ElMessage.success('已完成')
      load()
    }
    const reapply = (p) => Object.assign(applyDialog, {
      visible: true, targetType: p.target_type, targetId: p.target_id, targetName: p.target_name,
    })

    Vue.onMounted(load)
    return {
      tab, loading, invitations, approvals, granted, applied, todoCount, applyDialog,
      load, decide, remove, reapply, targetLink, formatTime, TARGET_LABEL, PERMISSION_TYPE_LABEL, PERMISSION_STATUS,
    }
  },
}

// ---------------- 打开邀请链接 ----------------
// /#/invite/{token}：未登录时先登录 / 注册再回到这里；获得权限后跳转到目标知识库 / 文档
const InviteLinkPage = {
  template: `
  <div class="page">
    <el-result v-if="error" icon="warning" title="无法使用该邀请链接" :sub-title="error">
      <template #extra><el-button type="primary" @click="$router.replace('/kb')">返回知识库</el-button></template>
    </el-result>
    <div v-else v-loading="true" element-loading-text="正在加入..." class="invite-loading" />
  </div>`,
  setup() {
    const route = VueRouter.useRoute()
    const router = VueRouter.useRouter()
    const error = Vue.ref('')

    Vue.onMounted(async () => {
      try {
        const r = await api.redeemInviteLink(route.params.token)
        const target = `${TARGET_LABEL[r.target_type]}「${r.target_name}」`
        if (r.granted) ElMessage.success(`已获得${target}的${PERMISSION_TYPE_LABEL[r.permission_type]}权限`)
        else ElMessage.info(`你已拥有${target}的${PERM_LABEL[r.my_permission]}权限`)
        refreshPermissionBadge()
        router.replace(targetLink(r))
      } catch (e) {
        error.value = e.message
      }
    })
    return { error }
  },
}
