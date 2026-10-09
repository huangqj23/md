import type { AgentArticle, PublishPlatformId } from '@/publish-agent/protocol'
import type { HostingReport } from '@/services/publish/hosting'
import type { LoginInfo, PublishPlatform } from '@/services/publish/platforms'
import type { PublishRecord } from '@/services/publish/records'
import type { PlatformRun, RunStatus } from '@/services/publish/runner'
import type { UploadProviderId } from '@/services/upload/provider-registry'
import { useImageUploader } from '@/composables/useImageUploader'
import { getLocale, t } from '@/i18n/translate'
import { PUBLISH_PLATFORM_IDS } from '@/publish-agent/protocol'
import { processClipboardContent } from '@/services/export'
import { auditImages, buildAgentArticle, extractMarkdownTitle, extractSummary, withoutWechatOnly } from '@/services/publish/article'
import { isLiveStatus } from '@/services/publish/center'
import { createPublishTabsApi, getExtensionGlobal } from '@/services/publish/extension-api'
import { renderFormulaImage, withDisplayFormulaImages, withPlainFormulas } from '@/services/publish/formulas'
import { canHostImages, createImageHosting, toUploadFile } from '@/services/publish/hosting'
import { createDetectContext, detectLoginStates } from '@/services/publish/login'
import { hasPublishPermissions, matchesPattern, requestPublishPermissions } from '@/services/publish/permissions'
import { getPublishPlatform, PUBLISH_PLATFORMS } from '@/services/publish/platforms'
import { applyRun, beginSync, contentHash, markPublished, unmarkPublished } from '@/services/publish/records'
import { runPublish } from '@/services/publish/runner'
import { htmlBytes, splitArticle } from '@/services/publish/split'
import { renderTableImage, withTablesAsImages } from '@/services/publish/tables'
import { resolveUploadProvider } from '@/services/upload/provider-registry'
import { store } from '@/storage'
import { addPrefix } from '@/storage/prefix'
import { useEditorStore } from '@/stores/editor'
import { usePostStore } from '@/stores/post'
import { useRenderStore } from '@/stores/render'
import { useThemeStore } from '@/stores/theme'

const DEFAULT_SELECTION: PublishPlatformId[] = [`wechat`, `zhihu`, `csdn`, `juejin`]

export interface ImageHostingState extends HostingReport {
  postId: string
  host: UploadProviderId
  /** Every image the sync needs is on the image host or kept embedded; the platforms come next. */
  finished: boolean
}

export interface PublishInput {
  title: string
  summary: string
  /** Defaults to the platforms selected in the publish dialog. */
  platformIds?: readonly PublishPlatformId[]
}

/** Part titles and the lines that point readers to the other parts, in the UI language. */
function splitMessages() {
  const label = (index: number, total: number) => {
    const names = total === 2 ? [`upper`, `lower`] : total === 3 ? [`upper`, `middle`, `lower`] : []
    return t(`publish.split.labels.${names[index] ?? `numbered`}`, { index: index + 1, total })
  }
  return {
    partTitle: (title: string, index: number, total: number) => t(`publish.split.title`, { title, label: label(index, total) }),
    continuedIn: (title: string) => t(`publish.split.continued`, { title }),
    continuedFrom: (title: string) => t(`publish.split.continuedFrom`, { title }),
  }
}

/** Splits the article for each platform it is too long for; platforms not listed get it whole. */
async function splitForLimits(platforms: readonly PublishPlatform[], article: AgentArticle): Promise<Partial<Record<PublishPlatformId, AgentArticle[]>>> {
  const result: Partial<Record<PublishPlatformId, AgentArticle[]>> = {}
  for (const platform of platforms) {
    const limit = platform.bodyMaxBytes ?? platform.bodyMaxLength
    const measure = platform.bodyMaxBytes ? htmlBytes : undefined
    if (!limit || (measure ? await measure(article.markdown) : article.textLength) <= limit)
      continue
    const parts = await splitArticle(article, { limit, measure, titleMaxLength: platform.titleMaxLength, ...splitMessages() })
    if (parts)
      result[platform.id] = parts
    else
      console.warn(`[publish] ${platform.id}: article is over the length limit and has no headings to split at`)
  }
  return result
}

/** Drawings already made, by the article drawn on and how; platforms that draw alike share them. */
type Drawings = WeakMap<AgentArticle, Map<string, Promise<AgentArticle>>>

function needsDrawing(platform: PublishPlatform): boolean {
  return Boolean(platform.tablesAsImages || platform.plainFormulas || platform.displayFormulasAsImages)
}

/**
 * Editors without tables get every table drawn as an image, editors that strip SVG get display
 * formulas drawn as images and inline ones as text, and editors without formulas that take the
 * plain HTML get display formulas drawn as images.
 */
async function drawnFor(platform: PublishPlatform, source: AgentArticle, drawings: Drawings): Promise<AgentArticle> {
  const { table, formula } = platform.pictures ?? {}
  const draw = (from: AgentArticle, key: string, work: () => Promise<AgentArticle>) => {
    let done = drawings.get(from)
    if (!done)
      drawings.set(from, done = new Map())
    if (!done.has(key))
      done.set(key, work())
    return done.get(key)!
  }
  let prepared = source
  if (platform.tablesAsImages) {
    const from = prepared
    prepared = await draw(from, `tables${JSON.stringify(table ?? null)}`, () => withTablesAsImages(from, table && (cells => renderTableImage(cells, table))))
  }
  if (platform.plainFormulas) {
    const from = prepared
    prepared = await draw(from, `plain${JSON.stringify(formula ?? null)}`, () => withPlainFormulas(from, formula && (svg => renderFormulaImage(svg, formula))))
  }
  if (platform.displayFormulasAsImages) {
    const from = prepared
    prepared = await draw(from, `display${JSON.stringify(formula ?? null)}`, () => withDisplayFormulaImages(from, formula && (svg => renderFormulaImage(svg, formula))))
  }
  return prepared
}

/** WeChat keeps the article with its QR code; the others that need tables or formulas drawn get them drawn. */
async function articlesByPlatform(
  platforms: readonly PublishPlatform[],
  article: AgentArticle,
  elsewhere: AgentArticle,
  drawings: Drawings,
): Promise<Partial<Record<PublishPlatformId, AgentArticle>>> {
  const result: Partial<Record<PublishPlatformId, AgentArticle>> = { wechat: article }
  for (const platform of platforms) {
    if (needsDrawing(platform))
      result[platform.id] = await drawnFor(platform, elsewhere, drawings)
  }
  return result
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
  /** The latest sync's uploads to the image host, for the publish center; null when it uploaded nothing. */
  const imageHosting = ref<ImageHostingState | null>(null)

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

  /**
   * The image host set up in the editor's image-host settings, when it is one every platform can
   * fetch from: embedded images then go there before syncing. Null keeps them embedded.
   */
  async function imageHost(): Promise<UploadProviderId | null> {
    const provider = resolveUploadProvider(await store.get(`imgHost`)).id
    return canHostImages(provider) ? provider : null
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
    const output = renderedOutput()
    const audit = output ? auditImages(output) : null
    const host = await imageHost()
    // Images only WeChat can take: local paths, and embedded ones unless they go to the image host.
    let unsupportedImages = audit ? audit.local + (host ? 0 : audit.embedded) : 0
    const defaults = articleDefaults()

    running.value = true
    cancelRequested.value = false
    runPostId.value = postId
    imageHosting.value = null
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
      const built = await buildAgentArticle({
        title: input.title,
        summary: input.summary,
        markdown,
        wechatHtml: await collectWechatHtml(),
      })
      // Every platform gets links to the image host; those that keep images on their own host
      // (WeChat, Toutiao, Baijiahao, Bilibili) fetch them from there.
      const hosting = host
        ? createImageHosting(async dataUrl => useImageUploader().upload(await toUploadFile(dataUrl)), {
            onProgress: (report) => {
              imageHosting.value = { ...report, postId, host, finished: false }
            },
          })
        : null
      const article = hosting ? await hosting.hostArticle(built) : built
      // Only the WeChat article keeps the account's QR code; other platforms count it as off-site promotion.
      const elsewhere = withoutWechatOnly(article)
      const drawings: Drawings = new WeakMap()
      const articles = await articlesByPlatform(platforms, article, elsewhere, drawings)
      // Each part is drawn for its platform like a whole article would be.
      const parts = await splitForLimits(platforms, elsewhere)
      for (const platform of platforms) {
        const split = parts[platform.id]
        if (!split || !needsDrawing(platform))
          continue
        const drawn: AgentArticle[] = []
        for (const part of split)
          drawn.push(await drawnFor(platform, part, drawings))
        parts[platform.id] = drawn
      }
      if (hosting && host) {
        // Tables and formulas drawn as pictures for some platforms come back embedded.
        for (const [id, prepared] of Object.entries(articles) as [PublishPlatformId, AgentArticle][])
          articles[id] = await hosting.hostArticle(prepared)
        for (const [id, split] of Object.entries(parts) as [PublishPlatformId, AgentArticle[]][]) {
          const hosted: AgentArticle[] = []
          for (const part of split)
            hosted.push(await hosting.hostArticle(part))
          parts[id] = hosted
        }
        const report = hosting.report()
        imageHosting.value = report.total ? { ...report, postId, host, finished: true } : null
        unsupportedImages = (audit?.local ?? 0) + report.failed
      }
      const time = new Date().toLocaleTimeString(getLocale(), { hour: `2-digit`, minute: `2-digit` })
      const results = await runPublish(platforms, elsewhere, {
        api: createPublishTabsApi(ext),
        groupTitle: t(`publish.groupTitle`, { time }),
        articles,
        parts,
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
   * Brings back the tab a platform (or one part of a split article) was filled in. False
   * when it is gone: tab ids restart with the browser, so a stored id may now belong to an unrelated tab.
   */
  async function openPlatformTab(postId: string, id: PublishPlatformId, part?: number): Promise<boolean> {
    const tabs = getExtensionGlobal()?.tabs
    const liveRun = runPostId.value === postId ? runs.value.find(run => run.id === id) : undefined
    const record = records.value[postId]?.platforms[id]
    const tabId = part === undefined
      ? liveRun?.tabId ?? record?.tabId
      : liveRun?.parts?.[part]?.tabId ?? record?.parts?.[part]?.tabId
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
    imageHosting,
    setSelected,
    hasPermissions,
    requestPermissions,
    refreshLoginStates,
    articleDefaults,
    imageHost,
    publish,
    resync,
    cancel,
    setPublished,
    openPlatformTab,
    liveRunsFor,
  }
})
