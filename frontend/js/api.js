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
      auth.logout()
      window.location.hash = '#/login'
    }
    ElementPlus.ElMessage.error(message)
    return Promise.reject(new Error(message))
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
}

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
  nickname: [{ max: 32, message: '昵称最多 32 个字符', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度 6-64 位', trigger: 'blur' },
    { pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/, message: '密码需同时包含字母和数字', trigger: 'blur' },
  ],
}
