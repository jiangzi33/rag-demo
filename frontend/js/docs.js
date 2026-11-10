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

// ---------------- 检索结果定位 ----------------
// 从内容检索跳转过来时（?chunk=id），在正文中找到该切分块对应的段落，高亮并滚动到那里。
// 切分块文本由后端从同一份 HTML 解析得到（见 backend/app/rag/html_parser.py）：章节以标题划分，
// 块不跨章节，列表 / 引用 / 待办 / 代码 / 表格带有 Markdown 标记。这里先按标题路径找到所在章节，
// 再把章节内的段落与去掉标记、去掉空白后的块文本比对。
const CHUNK_HEADINGS = 'h1, h2, h3, h4, h5, h6'
const CHUNK_BLOCKS = 'p, li, pre, blockquote, tr, div[data-w-e-type="todo"]'
const CHUNK_PROBE = 20 // 用块文本首尾各 20 个字符匹配被切开的长段落

const compactText = (s) => (s || '').replace(/\s+/g, '')

// 去掉切分时加上的标记：代码围栏、表格分隔行和竖线、引用 / 列表 / 待办前缀、图片占位
const chunkPlainText = (content) => compactText(content.split('\n')
  .filter((line) => !/^```/.test(line) && !/^\|( --- \|)+$/.test(line))
  .map((line) => line.trim()
    .replace(/^(> ?)+/, '')
    .replace(/^([-*]|\d+\.) /, '')
    .replace(/^\[[ x]\] /, '')
    .replace(/\[图片: [^\]]*\]/g, '')
    .replace(/(?<!\\)\|/g, '')
    .replace(/\\\|/g, '|'))
  .join(''))

// 段落自身的文字：列表项不含嵌套的子列表
const blockOwnText = (el) => {
  if (el.tagName !== 'LI') return compactText(el.textContent)
  const clone = el.cloneNode(true)
  clone.querySelectorAll('ul, ol').forEach((n) => n.remove())
  return compactText(clone.textContent)
}

// 与后端一致的段落粒度：表格按行；列表项内的段落、引用内的段落归到外层，避免重复
const isChunkBlock = (el) => {
  if (el.closest('td, th') && el.tagName !== 'TR') return false
  if (el.tagName !== 'LI' && el.parentElement?.closest('li')) return false
  if (el.tagName !== 'PRE' && el.closest('pre')) return false
  if (el.tagName === 'BLOCKQUOTE' && el.querySelector(CHUNK_BLOCKS)) return false
  return true
}

// 把正文按标题拆成章节：[{ path: ['安装', 'Linux'], blocks: [{ el, text }] }]
const splitSections = (container) => {
  const sections = [{ path: [], blocks: [] }]
  const stack = [] // [{ level, text }]
  container.querySelectorAll(`${CHUNK_HEADINGS}, ${CHUNK_BLOCKS}`).forEach((el) => {
    if (/^H[1-6]$/.test(el.tagName)) {
      const level = Number(el.tagName[1])
      while (stack.length && stack[stack.length - 1].level >= level) stack.pop()
      stack.push({ level, text: compactText(el.textContent) })
      sections.push({ path: stack.map((h) => h.text), blocks: [] })
    } else if (isChunkBlock(el)) {
      const text = blockOwnText(el)
      if (text) sections[sections.length - 1].blocks.push({ el, text })
    }
  })
  return sections
}

// 段落中与切分块重合的部分（去空白后的字符区间）；整段都在块中时返回 null。
// 长段落会被切成多个块，相邻块之间还有重叠，所以块可能只覆盖段落的开头、中间或结尾
const overlapRange = (text, target, head, tail) => {
  if (target.includes(text)) return null
  const inside = text.indexOf(target)
  if (inside >= 0) return [inside, inside + target.length]
  const start = text.indexOf(head)
  if (start >= 0) return [start, text.length] // 块从这里开始，延续到后面的段落
  return [0, text.indexOf(tail) + tail.length] // 块从前面的段落延续到这里结束
}

// 把去空白后的字符区间换算成 DOM Range（跨越 <strong> 等行内标签也可以）
const textRange = (el, [start, end]) => {
  const range = document.createRange()
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  let pos = 0
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (let i = 0; i < node.data.length; i++) {
      if (/\s/.test(node.data[i])) continue
      if (pos === start) range.setStart(node, i)
      pos += 1
      if (pos === end) {
        range.setEnd(node, i + 1)
        return range
      }
    }
  }
  return null
}

// 返回切分块对应的段落（按正文顺序）：[{ el, range }]，range 为 null 表示整段；找不到时返回空数组
const locateChunk = (container, chunk) => {
  const target = chunkPlainText(chunk.content)
  if (!target) return []
  const head = target.slice(0, CHUNK_PROBE)
  const tail = target.slice(-CHUNK_PROBE)
  const path = chunk.heading_path.map(compactText).join('\u0000')
  let best = []
  let bestSize = 0
  for (const section of splitSections(container)) {
    if (section.path.join('\u0000') !== path) continue
    // 段落完整出现在块中，或块的开头 / 结尾出现在段落中（长段落被切开、相邻块重叠）
    const hit = section.blocks.map((b) => target.includes(b.text) || b.text.includes(head) || b.text.includes(tail))
    // 取命中文字最多的连续一段，避免很短的段落在别处偶然命中
    let run = []
    let size = 0
    section.blocks.forEach((b, i) => {
      if (hit[i]) {
        run.push(b)
        size += b.text.length
        if (size > bestSize) [best, bestSize] = [run.slice(), size]
      } else {
        run = []
        size = 0
      }
    })
  }
  return best.map((b) => {
    // 列表项的文字不含子列表，与 DOM 文本节点对不上，整项高亮即可
    const part = b.el.tagName === 'LI' ? null : overlapRange(b.text, target, head, tail)
    return { el: b.el, range: part && textRange(b.el, part) }
  })
}

// 精确高亮段落中的一部分需要 CSS Custom Highlight API（Chrome 105+ / Safari 17.2+ / Firefox 140+），不支持时整段高亮
const CHUNK_HIGHLIGHT = 'chunk-hit'
const canHighlightRange = typeof Highlight !== 'undefined' && !!window.CSS?.highlights

const highlightChunk = (parts) => {
  const ranges = []
  parts.forEach(({ el, range }) => {
    if (range && canHighlightRange) ranges.push(range)
    else el.classList.add(CHUNK_HIGHLIGHT)
  })
  if (ranges.length) CSS.highlights.set(CHUNK_HIGHLIGHT, new Highlight(...ranges))
  // 滚动到块的开头：部分高亮时按文字位置，而不是整个长段落的顶部
  const first = parts[0]
  if (first.range && canHighlightRange) {
    const top = first.range.getBoundingClientRect().top + window.scrollY - window.innerHeight / 3
    window.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' })
  } else {
    first.el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

const clearChunkHighlight = (container) => {
  container?.querySelectorAll(`.${CHUNK_HIGHLIGHT}`).forEach((el) => el.classList.remove(CHUNK_HIGHLIGHT))
  if (canHighlightRange) CSS.highlights.delete(CHUNK_HIGHLIGHT)
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
        <div v-if="chunkNotice" class="chunk-notice">
          <span>已定位到检索片段<template v-if="chunkNotice !== true">：{{ chunkNotice }}</template></span>
          <el-button link type="primary" @click="clearChunk">取消高亮</el-button>
        </div>
        <div v-if="!empty" ref="contentEl" class="doc-content" v-html="html"></div>
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

    // 检索结果定位：?chunk=切分块 id
    const contentEl = ref(null)
    const chunkNotice = ref(null) // 定位成功时为章节路径（无章节时为 true）
    const clearChunk = () => {
      clearChunkHighlight(contentEl.value)
      chunkNotice.value = null
    }
    const showChunk = async () => {
      clearChunk()
      const chunkId = Number(route.query.chunk)
      if (!chunkId || !doc.value) return
      let chunk
      try {
        chunk = await api.getDocChunk(kbId, docId, chunkId)
      } catch {
        // 文档编辑后切分块会重建，旧链接中的片段已不存在
        ElMessage.info('文档已更新，原检索片段已失效')
        return
      }
      await Vue.nextTick()
      const parts = contentEl.value ? locateChunk(contentEl.value, chunk) : []
      if (!parts.length) {
        ElMessage.info('未能在正文中定位到检索片段')
        return
      }
      highlightChunk(parts)
      chunkNotice.value = chunk.heading_path.join(' › ') || true
    }
    Vue.watch(() => route.query.chunk, showChunk)
    // 高亮注册在全局，离开页面时清除
    Vue.onBeforeUnmount(() => clearChunkHighlight(null))

    const load = async () => {
      try {
        kb.value = await api.getKb(kbId)
        doc.value = await api.getDoc(kbId, docId)
        loading.value = false
        showChunk()
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
    return {
      PERMISSION_TYPE_LABEL, kb, doc, docId, loading, forbidden, html, empty, kbPath, members, applying, can, canDeleteDoc,
      remove, formatTime, contentEl, chunkNotice, clearChunk,
    }
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
