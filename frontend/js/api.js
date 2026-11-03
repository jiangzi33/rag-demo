// 登录态 + 接口封装
const TOKEN_KEY = 'kb_token'

const auth = Vue.reactive({
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: null,
  setLogin(data) {
    this.token = data.access_token
    this.user = data.user
    localStorage.setItem(TOKEN_KEY, data.access_token)
  },
  logout() {
    this.token = ''
    this.user = null
    localStorage.removeItem(TOKEN_KEY)
  },
})

const http = axios.create({ baseURL: '/api', timeout: 15000 })

http.interceptors.request.use((config) => {
  if (auth.token) config.headers.Authorization = `Bearer ${auth.token}`
  return config
})

http.interceptors.response.use(
  (res) => res.data.data,
  (err) => {
    const body = err.response?.data
    const message = body?.message || err.message || '网络异常'
    if (err.response?.status === 401 && auth.token) {
      if (typeof stopNotificationPolling === 'function') stopNotificationPolling()
      auth.logout()
      window.location.hash = '#/login'
    }
    // quiet: 403 由页面自行展示（如“没有读权限”的申请入口），不再弹出错误提示；silent: 任何错误都不提示（后台轮询）
    const muted = err.config?.silent || (err.config?.quiet && err.response?.status === 403)
    if (!muted) ElementPlus.ElMessage.error(message)
    const error = new Error(message)
    error.status = err.response?.status
    return Promise.reject(error)
  },
)

const api = {
  register: (data) => http.post('/auth/register', data),
  login: (data) => http.post('/auth/login', data),
  me: () => http.get('/users/me'),
  updateMe: (data) => http.put('/users/me', data),
  changePassword: (data) => http.put('/users/me/password', data),
  uploadAvatar: (file) => {
    const form = new FormData()
    form.append('file', file)
    return http.post('/users/me/avatar', form)
  },
  adminListUsers: (params) => http.get('/admin/users', { params }),
  adminSetStatus: (id, status) => http.patch(`/admin/users/${id}/status`, { status }),
  adminSetVip: (id, is_vip) => http.patch(`/admin/users/${id}/vip`, { is_vip }),
  // scope: all-全部知识库（默认） mine-我拥有或被授权的
  listKbs: (params) => http.get('/knowledge-bases', { params }),
  getKb: (id) => http.get(`/knowledge-bases/${id}`),
  createKb: (data) => http.post('/knowledge-bases', data),
  updateKb: (id, data) => http.put(`/knowledge-bases/${id}`, data),
  deleteKb: (id) => http.delete(`/knowledge-bases/${id}`),
  // 文档必须属于某个知识库，所有文档接口都带 kbId
  listDocs: (kbId, params) => http.get(`/knowledge-bases/${kbId}/documents`, { params }),
  getDoc: (kbId, id) => http.get(`/knowledge-bases/${kbId}/documents/${id}`, { quiet: true }),
  createDoc: (kbId, data) => http.post(`/knowledge-bases/${kbId}/documents`, data),
  updateDoc: (kbId, id, data) => http.put(`/knowledge-bases/${kbId}/documents/${id}`, data),
  deleteDoc: (kbId, id) => http.delete(`/knowledge-bases/${kbId}/documents/${id}`),
  uploadDocImage: (kbId, file) => {
    const form = new FormData()
    form.append('file', file)
    return http.post(`/knowledge-bases/${kbId}/documents/images`, form, { timeout: 60000 })
  },
  // 权限：target_type 为 kb / doc
  invitePermission: (data) => http.post('/permissions/invitations', data),
  applyPermission: (data) => http.post('/permissions/applications', data),
  myPermissions: (params) => http.get('/permissions/mine', { params }),
  pendingApprovals: () => http.get('/permissions/approvals'),
  targetPermissions: (targetType, targetId) =>
    http.get('/permissions', { params: { target_type: targetType, target_id: targetId } }),
  acceptPermission: (id) => http.post(`/permissions/${id}/accept`),
  rejectPermission: (id) => http.post(`/permissions/${id}/reject`),
  changePermissionType: (id, permission_type) => http.patch(`/permissions/${id}`, { permission_type }),
  removePermission: (id) => http.delete(`/permissions/${id}`),
  // 邀请链接：打开 /#/invite/{token} 即获得权限
  createInviteLink: (data) => http.post('/permissions/invite-links', data),
  listInviteLinks: (targetType, targetId) =>
    http.get('/permissions/invite-links', { params: { target_type: targetType, target_id: targetId } }),
  removeInviteLink: (id) => http.delete(`/permissions/invite-links/${id}`),
  redeemInviteLink: (token) => http.post(`/permissions/invite-links/${encodeURIComponent(token)}/redeem`, null, { silent: true }),
  // 通知：返回分页 + unread 未读总数
  listNotifications: (params, config = {}) => http.get('/notifications', { params, ...config }),
  readNotification: (id) => http.post(`/notifications/${id}/read`),
  readAllNotifications: () => http.post('/notifications/read-all'),
}

// 与后端 access_service.Level 保持一致：my_permission 为 owner / manage / write / read，无权限时为 null
// 所有人都能看到全部知识库和文档列表：读 —— 打开文档；写 —— 编辑；管理 —— 额外可删除、管理授权
const PERM_RANK = { read: 1, write: 2, manage: 3, owner: 4 }
const PERM_LABEL = { owner: '拥有者', manage: '可管理', write: '可编辑', read: '只读' }
const PERM_TAG = { owner: 'danger', manage: 'warning', write: 'success', read: 'info' }
const hasPerm = (myPermission, need) => (PERM_RANK[myPermission] || 0) >= PERM_RANK[need]
// 删除文档：需要管理权限
const canDeleteDoc = (doc) => hasPerm(doc.my_permission, 'manage')

// 与后端 PermissionType / PermissionStatus / TargetType 保持一致
const PERMISSION_TYPES = [
  { value: 'read', label: '读', desc: '查看内容' },
  { value: 'write', label: '写', desc: '查看、新建和编辑文档' },
  { value: 'manage', label: '管理', desc: '读写，并可删除文档、管理成员授权' },
]
const PERMISSION_TYPE_LABEL = { read: '读', write: '写', manage: '管理' }
const PERMISSION_STATUS = {
  pending: { text: '待处理', type: 'warning' },
  accept: { text: '已生效', type: 'success' },
  reject: { text: '已拒绝', type: 'info' },
}
const TARGET_LABEL = { kb: '知识库', doc: '文档' }

// 与后端 UserStatus 保持一致
const USER_STATUS = {
  0: { text: '禁用', type: 'danger' },
  1: { text: '正常', type: 'success' },
  2: { text: '已注销', type: 'info' },
}

const formatTime = (s) => (s ? s.replace('T', ' ').slice(0, 19) : '-')

// 与后端 schemas 中的校验规则保持一致
const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { pattern: /^[A-Za-z][A-Za-z0-9_]{3,31}$/, message: '4-32 位，字母开头，仅含字母/数字/下划线', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  kbName: [
    { required: true, whitespace: true, message: '请输入知识库名称', trigger: 'blur' },
    { max: 64, message: '名称最多 64 个字符', trigger: 'blur' },
  ],
  docTitle: [
    { required: true, whitespace: true, message: '请输入文档标题', trigger: 'blur' },
    { max: 128, message: '标题最多 128 个字符', trigger: 'blur' },
  ],
  nickname: [{ max: 32, message: '昵称最多 32 个字符', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度 6-64 位', trigger: 'blur' },
    { pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/, message: '密码需同时包含字母和数字', trigger: 'blur' },
  ],
}
