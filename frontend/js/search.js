// 内容检索：按语义相似度在知识库中搜索文档片段，类似搜索引擎
// 检索条件保存在地址栏（/search?q=...&kb=1,2&k=20），刷新、前进后退、分享链接都能还原结果
const SEARCH_TOP_K_OPTIONS = [5, 10, 20]
const SEARCH_DEFAULT_TOP_K = 10

const SearchPage = {
  template: `
  <div class="page search-page" :class="{ 'search-home': !searched }">
    <h2 v-if="!searched" class="search-logo">内容检索</h2>

    <div class="search-bar">
      <el-input v-model="form.q" size="large" clearable maxlength="1000" class="search-input"
                placeholder="搜索知识库中的内容，可以直接用一句话描述" @keyup.enter="submit">
        <template #append><el-button :loading="loading" @click="submit">搜索</el-button></template>
      </el-input>
      <div class="search-options">
        <el-select v-model="form.kbIds" multiple collapse-tags collapse-tags-tooltip filterable clearable
                   :loading="kbLoading" placeholder="全部知识库" size="small" class="search-kb-select">
          <el-option v-for="kb in kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
        </el-select>
        <el-select v-model="form.topK" size="small" class="search-topk">
          <el-option v-for="k in SEARCH_TOP_K_OPTIONS" :key="k" :label="'最多 ' + k + ' 条'" :value="k" />
        </el-select>
      </div>
      <p v-if="!kbLoading && !kbs.length" class="muted search-hint">
        你还没有任何知识库的权限，<router-link to="/kb">去知识库</router-link>创建或申请权限后再来检索。
      </p>
    </div>

    <div v-if="searched" v-loading="loading" class="search-results">
      <p v-if="!loading" class="muted search-stats">找到 {{ results.length }} 条相关结果（用时 {{ elapsed }} 秒）</p>
      <el-empty v-if="!loading && !results.length" description="没有找到相关内容，换个说法或多选几个知识库试试" />
      <div v-for="r in results" :key="r.chunk_id" class="search-item">
        <div class="muted search-crumb">
          {{ kbName(r.kb_id) }} › {{ r.doc_title }}<template v-if="r.heading"> › {{ r.heading.split(' > ').join(' › ') }}</template>
        </div>
        <router-link :to="{ path: '/kb/' + r.kb_id + '/docs/' + r.doc_id, query: { chunk: r.chunk_id } }" class="search-item-title">{{ itemTitle(r) }}</router-link>
        <div class="search-snippet" :class="{ expanded: expanded[r.chunk_id] }" :title="expanded[r.chunk_id] ? '' : '点击展开全文'"
             @click="expanded[r.chunk_id] = !expanded[r.chunk_id]">{{ r.content }}</div>
        <div class="muted search-meta">相关度 {{ (r.score * 100).toFixed(1) }}%</div>
      </div>
    </div>
  </div>`,
  setup() {
    const route = VueRouter.useRoute()
    const router = VueRouter.useRouter()
    const kbs = ref([])
    const kbLoading = ref(true)
    const loading = ref(false)
    const results = ref([])
    const searched = ref('') // 当前结果对应的检索内容，为空时显示首页样式
    const elapsed = ref('0.00')
    const expanded = reactive({})
    const form = reactive({ q: '', kbIds: [], topK: SEARCH_DEFAULT_TOP_K })
    let seq = 0 // 只采用最后一次检索的结果，避免慢请求覆盖新结果

    const kbName = (id) => kbs.value.find((kb) => kb.id === id)?.name || '知识库'
    // 标题：最深一级章节 - 文档标题，没有章节时只显示文档标题
    const itemTitle = (r) => (r.heading ? `${r.heading.split(' > ').pop()} - ${r.doc_title}` : r.doc_title)

    const syncFromRoute = () => {
      const { q, kb, k } = route.query
      form.q = typeof q === 'string' ? q : ''
      const ids = String(kb || '').split(',').map(Number)
      form.kbIds = kbs.value.filter((x) => ids.includes(x.id)).map((x) => x.id)
      form.topK = SEARCH_TOP_K_OPTIONS.includes(Number(k)) ? Number(k) : SEARCH_DEFAULT_TOP_K
    }

    const run = async () => {
      syncFromRoute()
      const q = form.q.trim()
      if (!q) {
        searched.value = ''
        results.value = []
        return
      }
      // 不选知识库表示在全部有权限的知识库中检索
      const kbIds = form.kbIds.length ? form.kbIds : kbs.value.map((kb) => kb.id)
      const current = ++seq
      searched.value = q
      if (!kbIds.length) {
        results.value = []
        return
      }
      loading.value = true
      const start = performance.now()
      try {
        const data = await api.search({ query: q, kb_ids: kbIds, top_k: form.topK })
        if (current !== seq) return
        results.value = data
        elapsed.value = ((performance.now() - start) / 1000).toFixed(2)
        Object.keys(expanded).forEach((key) => delete expanded[key])
      } catch {
        if (current === seq) results.value = []
      } finally {
        if (current === seq) loading.value = false
      }
    }

    const submit = () => {
      const q = form.q.trim()
      if (!q) {
        ElMessage.warning('请输入要检索的内容')
        return
      }
      const query = {
        q,
        kb: form.kbIds.length ? form.kbIds.join(',') : undefined,
        k: form.topK !== SEARCH_DEFAULT_TOP_K ? String(form.topK) : undefined,
      }
      const same = JSON.stringify(query) === JSON.stringify({ q: route.query.q, kb: route.query.kb, k: route.query.k })
      // 条件没变时地址不会变化，直接重新检索
      if (same) run()
      else router.push({ path: '/search', query })
    }

    Vue.watch(
      () => route.query,
      () => {
        if (route.path === '/search' && !kbLoading.value) run()
      },
    )

    onMounted(async () => {
      try {
        // 只列出有权限的知识库（含只被授权部分文档的），检索范围在后端按权限再次限定
        kbs.value = (await api.listKbs({ scope: 'mine', size: 100 })).items
      } finally {
        kbLoading.value = false
      }
      run()
    })

    return {
      kbs, kbLoading, loading, results, searched, elapsed, expanded, form,
      kbName, itemTitle, submit, SEARCH_TOP_K_OPTIONS,
    }
  },
}
