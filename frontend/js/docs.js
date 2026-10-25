// 文档页面：阅读、新建、编辑。所有路由都挂在 /kb/:kbId/docs 下，文档不能脱离知识库存在。
// 正文为富文本 HTML（wangEditor 编辑），服务端已做白名单过滤，展示前再用 DOMPurify 过滤一次。

const sanitizeHtml = (html) => DOMPurify.sanitize(html || '', { ADD_ATTR: ['target'] })

// 正文是否为空：没有文字也没有图片 / 表格
const isEmptyHtml = (html) => {
  const div = document.createElement('div')
  div.innerHTML = sanitizeHtml(html)
  return !div.textContent.trim() && !div.querySelector('img, table, hr')
}

// 字体：中文字体在 Windows / macOS 名称不同，用字体栈保证两端都能显示。
// 取值里不能带引号：wangEditor 把取值直接拼进 data-value="..."，且再次加载时会去掉引号后按原值匹配本列表，
// 带空格的字体名不加引号在 CSS 中同样合法。编辑器自带“默认字体”选项，这里不再重复添加。
const FONT_FAMILIES = [
  { name: '微软雅黑', value: 'Microsoft YaHei, PingFang SC, sans-serif' },
  { name: '宋体', value: 'SimSun, Songti SC, serif' },
  { name: '黑体', value: 'SimHei, Heiti SC, sans-serif' },
  { name: '楷体', value: 'KaiTi, Kaiti SC, STKaiti, serif' },
  { name: '仿宋', value: 'FangSong, STFangsong, serif' },
  { name: 'Arial', value: 'Arial, sans-serif' },
  { name: 'Times New Roman', value: 'Times New Roman, serif' },
  { name: 'Courier New', value: 'Courier New, monospace' },
]
const FONT_SIZES = ['12px', '13px', '14px', '15px', '16px', '18px', '20px', '24px', '28px', '32px', '40px', '48px']
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
const IMAGE_MAX_BYTES = 10 * 1024 * 1024

const confirmDeleteDoc = async (kbId, doc) => {
  const ok = await ElMessageBox.confirm(
    `删除后不可恢复，确定要删除文档「${doc.title}」吗？`, '删除文档',
    { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' },
  ).catch(() => false)
  if (!ok) return false
  await api.deleteDoc(kbId, doc.id)
  ElMessage.success('已删除')
  return true
}

// 面包屑：知识库 / 某知识库 / 当前页
const DocBreadcrumb = {
  props: { kb: Object, current: String },
  template: `
  <el-breadcrumb separator="/" class="doc-breadcrumb">
    <el-breadcrumb-item :to="'/kb'">知识库</el-breadcrumb-item>
    <el-breadcrumb-item v-if="kb" :to="'/kb/' + kb.id">{{ kb.name }}</el-breadcrumb-item>
    <el-breadcrumb-item>{{ current }}</el-breadcrumb-item>
  </el-breadcrumb>`,
}

// ---------------- 文档阅读 ----------------
const DocViewPage = {
  components: { DocBreadcrumb, PermTag, PendingTag, PermissionDrawer, ApplyPermissionDialog, NoAccess },
  template: `
  <div class="page doc-page">
    <doc-breadcrumb :kb="kb" :current="doc?.title || '文档'" />
    <el-card shadow="never" v-loading="loading">
      <template v-if="doc">
        <div class="doc-head">
          <h1 class="doc-title">{{ doc.title }}</h1>
          <div class="doc-actions">
            <el-button v-if="can('write')" type="primary" @click="$router.push(kbPath + '/docs/' + doc.id + '/edit')">编辑</el-button>
            <el-button v-if="can('manage')" @click="members = true">权限</el-button>
            <el-button v-if="!can('manage') && doc.pending_permission" disabled>
              {{ PERMISSION_TYPE_LABEL[doc.pending_permission] }}权限审批中
            </el-button>
            <el-button v-else-if="!can('manage')" @click="applying = true">申请{{ can('write') ? '管理' : '编辑' }}权限</el-button>
            <el-button v-if="canDeleteDoc(doc)" type="danger" plain @click="remove">删除</el-button>
          </div>
        </div>
        <div class="doc-meta muted">
          <span v-if="doc.author" class="user-cell">
            <el-avatar :size="18" :src="doc.author.avatar">{{ doc.author.nickname.slice(0, 1) }}</el-avatar>
            {{ doc.author.nickname }}
          </span>
          <span v-else>作者已删除</span>
          <span>发布于 {{ formatTime(doc.published_at) }}</span>
          <span v-if="doc.updated_at !== doc.published_at">最近编辑 {{ formatTime(doc.updated_at) }}</span>
          <perm-tag :perm="doc.my_permission" />
          <pending-tag :perm="doc.pending_permission" />
        </div>
        <el-divider />
        <div v-if="!empty" class="doc-content" v-html="html"></div>
        <el-empty v-else description="这篇文档还没有内容" :image-size="80">
          <el-button v-if="can('write')" @click="$router.push(kbPath + '/docs/' + doc.id + '/edit')">开始编辑</el-button>
        </el-empty>
      </template>
      <!-- 文档存在但没有读权限（403）时显示申请入口；不存在（404）时只提示 -->
      <no-access v-else-if="!loading" target-type="doc" :target-id="docId" :forbidden="forbidden" need="read"
                 :back-to="kb ? kbPath : '/kb'" />
    </el-card>

    <template v-if="doc">
      <permission-drawer v-model="members" target-type="doc" :target-id="doc.id" :target-name="doc.title" />
      <apply-permission-dialog v-model="applying" target-type="doc" :target-id="doc.id" :target-name="doc.title"
                               :current="doc.my_permission" @applied="load" />
    </template>
  </div>`,
  setup() {
    const route = VueRouter.useRoute()
    const router = VueRouter.useRouter()
    const { kbId, docId } = route.params
    const kbPath = `/kb/${kbId}`
    const kb = ref(null)
    const doc = ref(null)
    const loading = ref(true)
    const forbidden = ref(false)
    const html = computed(() => sanitizeHtml(doc.value?.content))
    const empty = computed(() => isEmptyHtml(doc.value?.content))

    const members = ref(false)
    const applying = ref(false)
    const can = (need) => hasPerm(doc.value?.my_permission, need)

    const load = async () => {
      try {
        kb.value = await api.getKb(kbId)
        doc.value = await api.getDoc(kbId, docId)
      } catch (e) {
        doc.value = null
        forbidden.value = e.status === 403
      } finally {
        loading.value = false
      }
    }
    const remove = async () => {
      if (await confirmDeleteDoc(kbId, doc.value)) router.replace(kbPath)
    }

    onMounted(load)
    return { PERMISSION_TYPE_LABEL, kb, doc, docId, loading, forbidden, html, empty, kbPath, members, applying, can, canDeleteDoc, remove, formatTime }
  },
}

// ---------------- 文档新建 / 编辑（富文本） ----------------
// 路由 /kb/:kbId/docs/new 为新建，/kb/:kbId/docs/:docId/edit 为编辑
const DocEditPage = {
  components: { DocBreadcrumb, NoAccess },
  template: `
  <div class="page doc-page">
    <doc-breadcrumb :kb="kb" :current="isNew ? '新建文档' : '编辑文档'" />
    <el-card shadow="never" v-loading="loading" class="doc-edit-card">
      <template v-if="ready">
        <div class="editor-bar">
          <el-form ref="formRef" :model="form" :rules="formRules" class="editor-title-form" @submit.prevent>
            <el-form-item prop="title">
              <el-input v-model="form.title" placeholder="请输入文档标题" maxlength="128" class="doc-title-input" />
            </el-form-item>
          </el-form>
          <span class="muted hide-narrow">{{ wordCount }} 字 · ⌘/Ctrl + S 保存</span>
          <el-button @click="cancel">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">{{ isNew ? '发布' : '保存' }}</el-button>
        </div>
        <div class="rich-editor">
          <div ref="toolbarEl" class="rich-toolbar"></div>
          <div ref="editorEl" class="rich-body"></div>
        </div>
      </template>
      <!-- 没有写权限：新建需要知识库写权限，编辑需要文档写权限 -->
      <no-access v-else-if="!loading && denied" :target-type="isNew ? 'kb' : 'doc'" :target-id="isNew ? kbId : docId"
                 :target-name="isNew ? kb?.name : form.title" forbidden need="write" :current="current"
                 :back-to="isNew ? '/kb/' + kbId : '/kb/' + kbId + '/docs/' + docId" />
      <no-access v-else-if="!loading" :target-type="kb ? 'doc' : 'kb'" :target-id="kb ? docId : kbId"
                 :back-to="kb ? '/kb/' + kbId : '/kb'" />
    </el-card>
  </div>`,
  setup() {
    const route = VueRouter.useRoute()
    const router = VueRouter.useRouter()
    const { kbId, docId } = route.params
    const isNew = !docId
    const kb = ref(null)
    const loading = ref(true)
    const ready = ref(false)
    const denied = ref(false) // 没有写权限
    const current = ref(null) // 没有写权限时的当前权限（read 或 null）
    const saving = ref(false)
    const formRef = ref()
    const toolbarEl = ref()
    const editorEl = ref()
    const form = reactive({ title: '', content: '' })
    const formRules = { title: rules.docTitle }
    const wordCount = ref(0)
    // 编辑器实例不放进响应式对象，避免 Vue 深度代理
    let editor = null

    // 记录已保存的内容，用来判断是否有未保存修改
    let saved = { title: '', content: '' }
    const dirty = () => editor && (form.title !== saved.title || form.content !== saved.content)

    const uploadImage = async (file, insertFn) => {
      if (!IMAGE_TYPES.includes(file.type)) return ElMessage.error('仅支持 png / jpg / gif / webp 格式的图片')
      if (file.size > IMAGE_MAX_BYTES) return ElMessage.error('图片不能超过 10MB')
      try {
        const { url } = await api.uploadDocImage(kbId, file)
        insertFn(url, file.name, '')
      } catch {
        // 错误提示已由 http 拦截器统一弹出
      }
    }

    const createEditor = () => {
      editor = wangEditor.createEditor({
        selector: editorEl.value,
        html: form.content || '<p><br></p>',
        mode: 'default',
        config: {
          placeholder: '开始写作：可以设置字体、字号、颜色，粘贴或拖入图片……',
          onChange(ed) {
            form.content = ed.isEmpty() ? '' : ed.getHtml()
            wordCount.value = ed.getText().replace(/\s/g, '').length
          },
          MENU_CONF: {
            fontFamily: { fontFamilyList: FONT_FAMILIES },
            fontSize: { fontSizeList: FONT_SIZES },
            // 本地图片（按钮选择 / 粘贴 / 拖入）统一上传到服务器，不内嵌 base64
            uploadImage: { allowedFileTypes: IMAGE_TYPES, maxFileSize: IMAGE_MAX_BYTES, base64LimitSize: 0, customUpload: uploadImage },
            insertImage: { checkImage: (src) => /^(https?:\/\/|\/uploads\/)/.test(src) || '图片地址需以 http:// 或 https:// 开头' },
          },
        },
      })
      wangEditor.createToolbar({
        editor,
        selector: toolbarEl.value,
        mode: 'default',
        config: { excludeKeys: ['group-video', 'insertVideo', 'uploadVideo', 'emotion', 'fullScreen'] },
      })
      // 以编辑器规范化后的 HTML 作为“已保存”基准
      form.content = editor.isEmpty() ? '' : editor.getHtml()
      wordCount.value = editor.getText().replace(/\s/g, '').length
      saved = { title: form.title, content: form.content }
      if (isNew) Vue.nextTick(() => document.querySelector('.doc-title-input input')?.focus())
    }

    const load = async () => {
      try {
        kb.value = await api.getKb(kbId)
        let myPermission = kb.value.my_permission
        if (!isNew) {
          const doc = await api.getDoc(kbId, docId)
          Object.assign(form, { title: doc.title, content: doc.content })
          myPermission = doc.my_permission
        }
        if (!hasPerm(myPermission, 'write')) {
          denied.value = true
          current.value = myPermission
          return
        }
        ready.value = true
      } catch (e) {
        denied.value = e.status === 403 // 403：连读权限都没有；否则显示“不存在”
        return
      } finally {
        loading.value = false
      }
      await Vue.nextTick()
      createEditor()
    }

    const save = async () => {
      if (saving.value || !(await formRef.value.validate().catch(() => false))) return
      saving.value = true
      try {
        const data = { title: form.title.trim(), content: form.content }
        const doc = isNew ? await api.createDoc(kbId, data) : await api.updateDoc(kbId, docId, data)
        saved = { title: form.title, content: form.content }
        ElMessage.success(isNew ? '发布成功' : '已保存')
        router.replace(`/kb/${kbId}/docs/${doc.id}`)
      } finally {
        saving.value = false
      }
    }
    const cancel = () => router.push(isNew ? `/kb/${kbId}` : `/kb/${kbId}/docs/${docId}`)

    // ⌘/Ctrl + S 保存（整个页面范围内生效）
    const onKeydown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault()
        save()
      }
    }

    // 离开页面前提示未保存的修改
    VueRouter.onBeforeRouteLeave(async () => {
      if (!dirty()) return true
      return ElMessageBox.confirm('有未保存的修改，确定离开吗？', '提示', {
        type: 'warning', confirmButtonText: '离开', cancelButtonText: '继续编辑',
      }).then(() => true).catch(() => false)
    })
    const beforeUnload = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = '' } }

    onMounted(() => {
      load()
      window.addEventListener('beforeunload', beforeUnload)
      window.addEventListener('keydown', onKeydown)
    })
    Vue.onBeforeUnmount(() => {
      window.removeEventListener('beforeunload', beforeUnload)
      window.removeEventListener('keydown', onKeydown)
      editor?.destroy()
      editor = null
    })

    return {
      kb, kbId, docId, isNew, loading, ready, denied, current, saving, formRef, toolbarEl, editorEl, form, formRules, wordCount,
      save, cancel,
    }
  },
}
