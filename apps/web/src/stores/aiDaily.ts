import type { LlmTestResult, LlmUpdate, LlmView } from '@/services/ai-daily/llm'
import type { DraftStatus, InboxList, JobStatus } from '@/services/ai-daily/native'
import { t } from '@/i18n/translate'
import { AiDailyError, callHost, hasNativePermission, isAiDailyDeclared, localDate, readEmbed, requestNativePermission, resolveLoadTarget } from '@/services/ai-daily/native'
import { getExtensionGlobal } from '@/services/publish/extension-api'
import { store } from '@/storage'
import { addPrefix } from '@/storage/prefix'
import { usePostStore } from '@/stores/post'

/** `unknown` until the first probe; the rest mirror AiDailyErrorKind plus `ok`. */
export type AiDailyConnection = `unknown` | `ok` | `unsupported` | `no-permission` | `not-installed` | `forbidden` | `host-error`

const JOB_POLL_MS = 2000

function nativeExt() {
  return getExtensionGlobal() as Parameters<typeof callHost>[0]
}

/** The AI daily panel needs the extension build that declares nativeMessaging (Chrome / Edge). */
export function isAiDailyAvailable(): boolean {
  return isAiDailyDeclared(nativeExt())
}

export const useAiDailyStore = defineStore(`aiDaily`, () => {
  const date = ref(localDate())
  const connection = ref<AiDailyConnection>(`unknown`)
  const connectionDetail = ref(``)
  const status = ref<DraftStatus | null>(null)
  const inbox = ref<InboxList | null>(null)
  const loading = ref(false)
  const job = ref<JobStatus>({ state: `none` })
  const llm = ref<LlmView | null>(null)
  /** Date → post id, so loading the same day again updates that post instead of adding another. */
  const loadedPosts = store.reactive<Record<string, string>>(addPrefix(`ai_daily_posts`), {})
  let pollTimer: ReturnType<typeof setTimeout> | undefined

  const extensionId = computed(() => getExtensionGlobal()?.runtime?.id ?? ``)

  function handleError(error: unknown): boolean {
    if (error instanceof AiDailyError && error.kind !== `command`) {
      connection.value = error.kind
      connectionDetail.value = error.message
      return true
    }
    toast.error(error instanceof Error ? error.message : String(error))
    return false
  }

  async function call<T>(request: { cmd: string, [key: string]: unknown }): Promise<T | null> {
    try {
      const reply = await callHost<T>(nativeExt(), request)
      connection.value = `ok`
      return reply
    }
    catch (error) {
      handleError(error)
      return null
    }
  }

  async function refresh() {
    const ext = nativeExt()
    if (!isAiDailyDeclared(ext)) {
      connection.value = `unsupported`
      return
    }
    if (!(await hasNativePermission(ext!))) {
      connection.value = `no-permission`
      return
    }
    loading.value = true
    try {
      const reply = await call<DraftStatus>({ cmd: `status`, date: date.value })
      if (!reply)
        return
      status.value = reply
      job.value = reply.job
      inbox.value = await call<InboxList>({ cmd: `inbox_list` })
      if (job.value.state === `running`)
        schedulePoll()
    }
    finally {
      loading.value = false
    }
  }

  /** Call first thing in a click handler; the browser needs the user gesture. */
  async function grant() {
    const ext = nativeExt()
    if (!ext || !(await requestNativePermission(ext))) {
      toast.error(t(`aiDaily.permission.denied`))
      return
    }
    await refresh()
  }

  function schedulePoll() {
    clearTimeout(pollTimer)
    pollTimer = setTimeout(pollJob, JOB_POLL_MS)
  }

  async function pollJob() {
    const reply = await call<{ job: JobStatus }>({ cmd: `job` })
    if (!reply)
      return
    const wasRunning = job.value.state === `running`
    job.value = reply.job
    if (reply.job.state === `running`) {
      schedulePoll()
      return
    }
    if (wasRunning) {
      const key = reply.job.kind === `publish` ? `publish` : `run`
      if (reply.job.state === `done`)
        toast.success(t(`aiDaily.job.${key}Done`))
      else
        toast.error(t(`aiDaily.job.${key}Failed`))
      await refresh()
    }
  }

  async function startRun(options: { force: boolean, noLlm: boolean }) {
    const reply = await call<{ job: JobStatus }>({ cmd: `run`, date: date.value, force: options.force, no_llm: options.noLlm })
    if (!reply)
      return
    job.value = reply.job
    schedulePoll()
  }

  /** Returns the blocking problems; an empty list means the embed job started. */
  async function startPublish(options: { skipLinks: boolean }): Promise<string[] | null> {
    const reply = await call<{ problems: string[], job?: JobStatus }>({ cmd: `publish`, date: date.value, skip_links: options.skipLinks })
    if (!reply)
      return null
    if (reply.job) {
      job.value = reply.job
      schedulePoll()
    }
    return reply.problems
  }

  async function loadIntoEditor(): Promise<boolean> {
    let embed: { title: string, content: string }
    try {
      embed = await readEmbed(nativeExt(), date.value)
    }
    catch (error) {
      handleError(error)
      return false
    }
    const postStore = usePostStore()
    const target = resolveLoadTarget(loadedPosts.value, date.value, id => !!postStore.getPostById(id))
    if (target) {
      postStore.updatePostContent(target.postId, embed.content)
      postStore.currentPostId = target.postId
    }
    else {
      postStore.addPost(embed.title || date.value)
      postStore.updatePostContent(postStore.currentPostId, embed.content)
      loadedPosts.value = { ...loadedPosts.value, [date.value]: postStore.currentPostId }
    }
    return true
  }

  async function open(which: `article` | `review` | `inbox`) {
    await call({ cmd: `open`, date: date.value, which })
  }

  async function addToInbox(url: string, note: string): Promise<boolean> {
    const reply = await call<{ added: boolean, count: number }>({ cmd: `inbox_add`, url, note })
    if (!reply)
      return false
    toast.success(reply.added ? t(`aiDaily.inbox.added`) : t(`aiDaily.inbox.duplicate`))
    inbox.value = await call<InboxList>({ cmd: `inbox_list` })
    return reply.added
  }

  async function loadLlm() {
    const reply = await call<LlmView>({ cmd: `llm_get` })
    if (reply)
      llm.value = reply
  }

  async function saveLlm(update: LlmUpdate): Promise<boolean> {
    const reply = await call<LlmView>({ cmd: `llm_set`, ...update })
    if (!reply)
      return false
    llm.value = reply
    if (status.value)
      status.value = { ...status.value, llm: reply.readiness }
    return true
  }

  /** Adds an OpenAI-compatible endpoint right away and returns its new id. */
  async function addCustomProvider(name: string, baseUrl: string, apiKey: string): Promise<string | null> {
    const before = new Set(llm.value?.providers.map(p => p.id) ?? [])
    const provider: Record<string, string> = { name, base_url: baseUrl }
    if (apiKey)
      provider.api_key = apiKey
    const reply = await call<LlmView>({ cmd: `llm_set`, providers: [provider] })
    if (!reply)
      return null
    llm.value = reply
    return reply.providers.find(p => !before.has(p.id))?.id ?? null
  }

  async function removeCustomProvider(id: string): Promise<boolean> {
    const reply = await call<LlmView>({ cmd: `llm_set`, remove: [id] })
    if (reply)
      llm.value = reply
    return !!reply
  }

  /** Connection failures come back as `success: false` with a reason, not as a thrown error. */
  async function testLlm(request: { provider: string, model: string, api_key?: string, base_url?: string }): Promise<LlmTestResult | null> {
    return call<LlmTestResult>({ cmd: `llm_test`, ...request })
  }

  function stopPolling() {
    clearTimeout(pollTimer)
  }

  return {
    date,
    connection,
    connectionDetail,
    status,
    inbox,
    loading,
    job,
    llm,
    extensionId,
    refresh,
    grant,
    startRun,
    startPublish,
    loadIntoEditor,
    open,
    addToInbox,
    loadLlm,
    saveLlm,
    addCustomProvider,
    removeCustomProvider,
    testLlm,
    stopPolling,
  }
})
