// 智能问答：聊天机器人。每个问题先检索最相关的文档片段，再由大模型整合生成回答（流式输出）。
// 回答中的 [1] [2] 是参考片段的序号，点击跳转到文档并高亮该片段。

// 对话保存在页面外，切换到其他页面再回来时不丢失；换了登录用户时清空
const qaStore = Vue.reactive({ userId: null, messages: [], kbIds: [] })

// 读取 SSE 流：每个事件为一行 data: {json}
const readSse = async (response, onEvent) => {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop()
    parts.forEach((part) => {
      const line = part.split('\n').find((l) => l.startsWith('data:'))
      if (line) onEvent(JSON.parse(line.slice(5)))
    })
  }
}

// 流式接口不走 axios（浏览器中的 axios 不支持逐块读取响应），登录态和错误处理与 http 拦截器保持一致
const streamChat = async (data, signal, onEvent) => {
  const res = await fetch('/api/qa/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.token}` },
    body: JSON.stringify(data),
    signal,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    if (res.status === 401 && auth.token) {
      stopNotificationPolling()
      auth.logout()
      window.location.hash = '#/login'
    }
    throw new Error(body?.message || `请求失败（${res.status}）`)
  }
  await readSse(res, onEvent)
}

const sourceLink = (s) => `#/kb/${s.kb_id}/docs/${s.doc_id}?chunk=${s.chunk_id}`

// 回答渲染为 Markdown；[1]、[1, 2]、[1][2] 中的序号替换为跳转到对应片段的链接，不存在的序号原样保留
const renderAnswer = (text, sources) => {
  const linked = (text || '').replace(/\[(\d+(?:\s*[,，、]\s*\d+)*)\]/g, (match, nums) => {
    const ids = nums.split(/\s*[,，、]\s*/).map(Number)
    if (!ids.every((n) => sources[n - 1])) return match
    return ids.map((n) => `<a class="qa-cite" href="${sourceLink(sources[n - 1])}" title="${escapeAttr(sources[n - 1].doc_title)}">${n}</a>`).join('')
  })
  return DOMPurify.sanitize(marked.parse(linked, { breaks: true }))
}
const escapeAttr = (s) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')

const QaPage = {
  template: `
  <div class="page qa-page">
    <div class="qa-head">
      <h2 class="page-title">智能问答</h2>
      <el-select v-model="store.kbIds" multiple collapse-tags collapse-tags-tooltip filterable clearable
                 :loading="kbLoading" placeholder="全部知识库" class="qa-kb-select">
        <el-option v-for="kb in kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
      </el-select>
      <el-button :disabled="!store.messages.length || streaming" @click="reset">新对话</el-button>
    </div>

    <div ref="listEl" class="qa-list" @scroll="onScroll">
      <div v-if="!store.messages.length" class="qa-welcome">
        <div class="qa-welcome-icon">🤖</div>
        <h3>有什么可以帮你？</h3>
        <p class="muted">我会先在知识库中检索相关内容，再整理成回答，并标注出处。只会用到你有阅读权限的文档。</p>
        <p v-if="!kbLoading && !kbs.length" class="muted">
          你还没有任何知识库的权限，<router-link to="/kb">去知识库</router-link>创建或申请权限后再来提问。
        </p>
      </div>

      <div v-for="(m, i) in store.messages" :key="i" class="qa-msg" :class="m.role">
        <div class="qa-avatar">
          <el-avatar v-if="m.role === 'user'" :size="32" :src="auth.user?.avatar">{{ auth.user?.nickname?.slice(0, 1) }}</el-avatar>
          <span v-else class="qa-bot">🤖</span>
        </div>
        <div class="qa-bubble">
          <div v-if="m.role === 'user'" class="qa-user-text">{{ m.content }}</div>
          <template v-else>
            <div v-if="m.status === 'searching'" class="muted qa-status"><span class="qa-dots"></span>正在检索知识库…</div>
            <div v-else-if="m.status === 'thinking'" class="muted qa-status"><span class="qa-dots"></span>找到 {{ m.sources.length }} 条相关内容，正在生成回答…</div>
            <div v-if="m.content" class="qa-answer doc-content" :class="{ streaming: m.status === 'streaming' }"
                 v-html="renderAnswer(m.content, m.sources)"></div>
            <el-alert v-if="m.error" :title="m.error" type="error" :closable="false" show-icon class="qa-error" />
            <div v-if="m.status === 'stopped'" class="muted qa-status">已停止生成</div>
            <div v-if="m.sources?.length && !['searching', 'thinking'].includes(m.status)" class="qa-sources">
              <div class="muted qa-sources-title">参考来源</div>
              <a v-for="(s, n) in m.sources" :key="s.chunk_id" :href="sourceLink(s)" class="qa-source">
                <span class="qa-source-no">{{ n + 1 }}</span>
                <span class="qa-source-title">{{ s.doc_title }}<template v-if="s.heading"> › {{ s.heading.split(' > ').join(' › ') }}</template></span>
                <span class="muted">{{ (s.score * 100).toFixed(0) }}%</span>
              </a>
            </div>
          </template>
        </div>
      </div>
    </div>

    <div class="qa-input">
      <el-input v-model="input" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }" resize="none" maxlength="1000"
                placeholder="输入问题，Enter 发送，Shift + Enter 换行" @keydown.enter.exact.prevent="send" />
      <el-button v-if="streaming" @click="stop">停止</el-button>
      <el-button v-else type="primary" :disabled="!input.trim()" @click="send">发送</el-button>
    </div>
  </div>`,
  setup() {
    const kbs = ref([])
    const kbLoading = ref(true)
    const input = ref('')
    const streaming = ref(false)
    const listEl = ref(null)
    let controller = null
    let stickToBottom = true // 用户向上翻看历史时不强制滚到底部

    if (qaStore.userId !== auth.user?.id) Object.assign(qaStore, { userId: auth.user?.id, messages: [], kbIds: [] })

    const scrollToBottom = async (force = false) => {
      await Vue.nextTick()
      const el = listEl.value
      if (el && (force || stickToBottom)) el.scrollTop = el.scrollHeight
    }
    const onScroll = () => {
      const el = listEl.value
      stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    }

    const send = async () => {
      const question = input.value.trim()
      if (!question || streaming.value) return
      const kbIds = qaStore.kbIds.length ? qaStore.kbIds : kbs.value.map((kb) => kb.id)
      if (!kbIds.length) {
        ElMessage.warning('还没有可以提问的知识库')
        return
      }
      // 只携带正常完成的对话作为上下文
      const history = qaStore.messages
        .filter((m) => m.role === 'user' || (m.status === 'done' && m.content))
        .map((m) => ({ role: m.role, content: m.content }))
        .slice(-6)
      qaStore.messages.push({ role: 'user', content: question })
      qaStore.messages.push({ role: 'assistant', content: '', sources: [], status: 'searching', error: '' })
      const answer = qaStore.messages[qaStore.messages.length - 1]
      input.value = ''
      streaming.value = true
      controller = new AbortController()
      scrollToBottom(true)
      try {
        await streamChat({ question, kb_ids: kbIds, history }, controller.signal, (e) => {
          if (e.type === 'sources') {
            answer.sources = e.sources
            answer.status = 'thinking'
          } else if (e.type === 'delta') {
            answer.content += e.text
            answer.status = 'streaming'
          } else if (e.type === 'error') {
            answer.error = e.message
            answer.status = 'error'
          } else if (e.type === 'done') {
            answer.status = 'done'
          }
          scrollToBottom()
        })
        if (!['done', 'error'].includes(answer.status)) {
          answer.status = 'error'
          answer.error = '连接意外中断，回答可能不完整'
        }
      } catch (err) {
        if (err.name === 'AbortError') {
          answer.status = 'stopped'
        } else {
          answer.status = 'error'
          answer.error = err.message || '网络异常'
        }
      } finally {
        streaming.value = false
        controller = null
        scrollToBottom()
      }
    }

    const stop = () => controller?.abort()
    const reset = () => {
      qaStore.messages = []
      input.value = ''
    }

    onMounted(async () => {
      scrollToBottom(true)
      try {
        // 只列出有权限的知识库（含只被授权部分文档的），检索范围在后端按权限再次限定
        kbs.value = (await api.listKbs({ scope: 'mine', size: 100 })).items
        qaStore.kbIds = qaStore.kbIds.filter((id) => kbs.value.some((kb) => kb.id === id))
      } finally {
        kbLoading.value = false
      }
    })
    // 离开页面时停止生成，已输出的内容保留
    Vue.onBeforeUnmount(stop)

    return {
      auth, store: qaStore, kbs, kbLoading, input, streaming, listEl,
      send, stop, reset, onScroll, renderAnswer, sourceLink,
    }
  },
}
