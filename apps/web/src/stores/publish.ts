import type { PublishPlatformId } from '@/publish-agent/protocol'
import type { LoginInfo, PublishPlatform } from '@/services/publish/platforms'
import type { PublishRecord } from '@/services/publish/records'
import type { PlatformRun, RunStatus } from '@/services/publish/runner'
import { getLocale, t } from '@/i18n/translate'
import { PUBLISH_PLATFORM_IDS } from '@/publish-agent/protocol'
import { processClipboardContent } from '@/services/export'
import { auditImages, buildAgentArticle, extractMarkdownTitle, extractSummary } from '@/services/publish/article'
import { isLiveStatus } from '@/services/publish/center'
import { createPublishTabsApi, getExtensionGlobal } from '@/services/publish/extension-api'
import { createDetectContext, detectLoginStates } from '@/services/publish/login'
import { hasPublishPermissions, matchesPattern, requestPublishPermissions } from '@/services/publish/permissions'
import { getPublishPlatform, PUBLISH_PLATFORMS } from '@/services/publish/platforms'
import { applyRun, beginSync, contentHash, markPublished, unmarkPublished } from '@/services/publish/records'
import { runPublish } from '@/services/publish/runner'
import { store } from '@/storage'
import { addPrefix } from '@/storage/prefix'
import { useEditorStore } from '@/stores/editor'
import { usePostStore } from '@/stores/post'
import { useRenderStore } from '@/stores/render'
import { useThemeStore } from '@/stores/theme'

const DEFAULT_SELECTION: PublishPlatformId[] = [`wechat`, `zhihu`, `csdn`, `juejin`]

export interface PublishInput {
  title: string
  summary: string
  /** Defaults to the platforms selected in the publish dialog. */
  platformIds?: readonly PublishPlatformId[]
}

/** Native multi-platform publishing is only possible from the extension's own pages. */
export function isNativePublishAvailable(): boolean {
  return getExtensionGlobal()?.tabs !== undefined
}

function renderedOutput(): Element | null {
  return document.querySelector(`#output`)
}

export const usePublishStore = defineStore(`publish`, () => {
  const selectedIds = store.reactive<PublishPlatformId[]>(addPrefix(`publish_platforms`), DEFAULT_SELECTION)
  /** Publish records by post id. Local only: tab ids and drafts belong to this browser. */
  const records = store.reactive<Record<string, PublishRecord>>(addPrefix(`publish_records`), {})
  const loginStates = ref<Partial<Record<PublishPlatformId, LoginInfo>>>({})
  const checkingLogin = ref(false)
  const runs = ref<PlatformRun[]>([])
  /** Post the latest sync belongs to, so the publish center can show its progress. */
  const runPostId = ref<string | null>(null)
  const running = ref(false)
  const cancelRequested = ref(false)

  const selectedPlatforms = computed<PublishPlatform[]>(() =>
    PUBLISH_PLATFORMS.filter(platform => selectedIds.value.includes(platform.id)),
  )

  function setSelected(id: PublishPlatformId, selected: boolean) {
    const next = new Set(selectedIds.value.filter(value => (PUBLISH_PLATFORM_IDS as readonly string[]).includes(value)))
    if (selected)
      next.add(id)
    else
      next.delete(id)
    selectedIds.value = PUBLISH_PLATFORM_IDS.filter(value => next.has(value))
  }

  async function hasPermissions(platforms: readonly PublishPlatform[] = PUBLISH_PLATFORMS): Promise<boolean> {
    const ext = getExtensionGlobal()
    return ext ? hasPublishPermissions(ext, platforms) : false
  }

  /** Call first thing in a click handler; the browser needs the user gesture. */
  async function requestPermissions(platforms: readonly PublishPlatform[] = PUBLISH_PLATFORMS): Promise<boolean> {
    const ext = getExtensionGlobal()
    return ext ? requestPublishPermissions(ext, platforms) : false
  }

  async function refreshLoginStates() {
    const ext = getExtensionGlobal()
    if (!ext || checkingLogin.value)
      return
    checkingLogin.value = true
    try {
      await detectLoginStates(PUBLISH_PLATFORMS, createDetectContext(ext), (id, info) => {
        loginStates.value = { ...loginStates.value, [id]: info }
      })
    }
    finally {
      checkingLogin.value = false
    }
  }

  /** Title and summary the publish dialog suggests for the current article. */
  function articleDefaults(): { title: string, summary: string } {
    const output = renderedOutput()
    return {
      title: extractMarkdownTitle(useEditorStore().getContent()) ?? usePostStore().currentPost?.title ?? ``,
      summary: output ? extractSummary(output) : ``,
    }
  }

  /** Images only WeChat can take: embedded (data:) ones and local paths. */
  function countUnsupportedImages(): number {
    const output = renderedOutput()
    if (!output)
      return 0
    const audit = auditImages(output)
    return audit.embedded + audit.local
  }

  /** WeChat-styled HTML, produced exactly like the "copy" button does. */
  async function collectWechatHtml(): Promise<string> {
    const themeStore = useThemeStore()
    try {
      return (await processClipboardContent(themeStore.primaryColor)).html
    }
    catch (error) {
      console.warn(`[publish] WeChat HTML unavailable, falling back to plain HTML`, error)
      return ``
    }
    finally {
      // Same as useEditorRefresh().editorRefresh(), which cannot run outside setup.
      themeStore.updateCodeTheme()
      useRenderStore().render(useEditorStore().getContent())
    }
  }

  function saveRecord(record: PublishRecord) {
    records.value = { ...records.value, [record.postId]: record }
  }

  function announce(results: readonly PlatformRun[]) {
    const count = (statuses: RunStatus[]) => results.filter(run => statuses.includes(run.status)).length
    toast.success(t(`publish.result`, {
      success: count([`success`]),
      warning: count([`warning`]),
      failed: count([`failed`, `login-required`, `cancelled`]),
    }))
  }

  /** Fills the current article into the given platforms and records each outcome on the post. */
  async function publish(input: PublishInput): Promise<PlatformRun[]> {
    const ext = getExtensionGlobal()
    const ids = input.platformIds
    const platforms = ids ? PUBLISH_PLATFORMS.filter(platform => ids.includes(platform.id)) : selectedPlatforms.value
    if (!ext || running.value || platforms.length === 0)
      return []

    const editorStore = useEditorStore()
    const postStore = usePostStore()
    // The record compares this hash with the post content later, so both must see the same text.
    editorStore.flushContentToPostStore()
    const postId = postStore.currentPostId
    const markdown = editorStore.getContent()
    const version = contentHash(markdown)
    const unsupportedImages = countUnsupportedImages()
    const defaults = articleDefaults()

    running.value = true
    cancelRequested.value = false
    runPostId.value = postId
    runs.value = platforms.map(platform => ({ id: platform.id, status: `queued`, warnings: [] }))
    saveRecord(beginSync(records.value[postId], {
      postId,
      title: input.title,
      summary: input.summary,
      customTitle: input.title !== defaults.title,
      customSummary: input.summary !== defaults.summary,
      version,
      at: Date.now(),
    }))
    try {
      const article = await buildAgentArticle({
        title: input.title,
        summary: input.summary,
        markdown,
        wechatHtml: await collectWechatHtml(),
      })
      const time = new Date().toLocaleTimeString(getLocale(), { hour: `2-digit`, minute: `2-digit` })
      const results = await runPublish(platforms, article, {
        api: createPublishTabsApi(ext),
        groupTitle: t(`publish.groupTitle`, { time }),
        shouldCancel: () => cancelRequested.value,
        onUpdate: (run) => {
          const index = runs.value.findIndex(item => item.id === run.id)
          if (index !== -1)
            runs.value[index] = run
          const record = records.value[postId]
          const next = record && applyRun(record, run, { version, at: Date.now(), unsupportedImages })
          if (next && next !== record)
            saveRecord(next)
        },
      })
      announce(results)
      return results
    }
    catch (error) {
      // The dialog that started the sync is closed by now, so report here.
      console.error(`[publish] sync failed before reaching the platforms`, error)
      toast.error(`${t(`publish.errors.generic`)}: ${error instanceof Error ? error.message : String(error)}`)
      return []
    }
    finally {
      running.value = false
    }
  }

  /**
   * Syncs the current article to some platforms again, with the title and summary of
   * its last sync unless those were the defaults, which follow the edited article.
   */
  async function resync(ids: readonly PublishPlatformId[]): Promise<PlatformRun[]> {
    const record = records.value[usePostStore().currentPostId]
    const defaults = articleDefaults()
    const title = record?.customTitle ? record.title : defaults.title
    if (!title.trim()) {
      toast.error(t(`publish.noTitle`))
      return []
    }
    return publish({
      title,
      summary: record?.customSummary ? record.summary : defaults.summary,
      platformIds: ids,
    })
  }

  function cancel() {
    cancelRequested.value = true
  }

  function setPublished(postId: string, id: PublishPlatformId, published: boolean, url?: string) {
    const record = records.value[postId]
    if (!record)
      return
    saveRecord(published ? markPublished(record, id, Date.now(), url) : unmarkPublished(record, id, Date.now()))
  }

  /**
   * Brings back the tab a platform was filled in. False when it is gone: tab ids
   * restart with the browser, so a stored id may now belong to an unrelated tab.
   */
  async function openPlatformTab(postId: string, id: PublishPlatformId): Promise<boolean> {
    const tabs = getExtensionGlobal()?.tabs
    const liveTabId = runPostId.value === postId ? runs.value.find(run => run.id === id)?.tabId : undefined
    const tabId = liveTabId ?? records.value[postId]?.platforms[id]?.tabId
    if (!tabs || tabId === undefined)
      return false
    try {
      const tab = await tabs.get(tabId)
      const url = tab.url
      if (!url || !getPublishPlatform(id).hostPermissions.some(pattern => matchesPattern(url, pattern)))
        return false
      await tabs.update(tabId, { active: true })
      return true
    }
    catch {
      return false
    }
  }

  /** Runs of the sync in progress for a post, for the publish center's live view. */
  function liveRunsFor(postId: string): PlatformRun[] {
    if (!running.value || runPostId.value !== postId)
      return []
    return runs.value.filter(run => isLiveStatus(run.status))
  }

  return {
    selectedIds,
    selectedPlatforms,
    records,
    loginStates,
    checkingLogin,
    runs,
    runPostId,
    running,
    cancelRequested,
    setSelected,
    hasPermissions,
    requestPermissions,
    refreshLoginStates,
    articleDefaults,
    publish,
    resync,
    cancel,
    setPublished,
    openPlatformTab,
    liveRunsFor,
  }
})
