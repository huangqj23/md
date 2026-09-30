import type { PublishPlatformId } from '@/publish-agent/protocol'
import type { LoginInfo, PublishPlatform } from '@/services/publish/platforms'
import type { PlatformRun } from '@/services/publish/runner'
import { PUBLISH_PLATFORM_IDS } from '@/publish-agent/protocol'
import { processClipboardContent } from '@/services/export'
import { buildAgentArticle } from '@/services/publish/article'
import { createPublishTabsApi, getExtensionGlobal } from '@/services/publish/extension-api'
import { createDetectContext, detectLoginStates } from '@/services/publish/login'
import { hasPublishPermissions, requestPublishPermissions } from '@/services/publish/permissions'
import { PUBLISH_PLATFORMS } from '@/services/publish/platforms'
import { runPublish } from '@/services/publish/runner'
import { store } from '@/storage'
import { addPrefix } from '@/storage/prefix'
import { useEditorStore } from '@/stores/editor'
import { useRenderStore } from '@/stores/render'
import { useThemeStore } from '@/stores/theme'

const DEFAULT_SELECTION: PublishPlatformId[] = [`wechat`, `zhihu`, `csdn`, `juejin`]

export interface PublishInput {
  title: string
  summary: string
  /** Name of the browser tab group that collects the opened platform tabs. */
  groupTitle: string
}

/** Native multi-platform publishing is only possible from the extension's own pages. */
export function isNativePublishAvailable(): boolean {
  return getExtensionGlobal()?.tabs !== undefined
}

export const usePublishStore = defineStore(`publish`, () => {
  const selectedIds = store.reactive<PublishPlatformId[]>(addPrefix(`publish_platforms`), DEFAULT_SELECTION)
  const loginStates = ref<Partial<Record<PublishPlatformId, LoginInfo>>>({})
  const checkingLogin = ref(false)
  const runs = ref<PlatformRun[]>([])
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

  async function publish(input: PublishInput): Promise<PlatformRun[]> {
    const ext = getExtensionGlobal()
    const platforms = selectedPlatforms.value
    if (!ext || running.value || platforms.length === 0)
      return []

    running.value = true
    cancelRequested.value = false
    runs.value = platforms.map(platform => ({ id: platform.id, status: `queued`, warnings: [] }))
    try {
      const article = await buildAgentArticle({
        title: input.title,
        summary: input.summary,
        markdown: useEditorStore().getContent(),
        wechatHtml: await collectWechatHtml(),
      })
      return await runPublish(platforms, article, {
        api: createPublishTabsApi(ext),
        groupTitle: input.groupTitle,
        shouldCancel: () => cancelRequested.value,
        onUpdate: (run) => {
          const index = runs.value.findIndex(item => item.id === run.id)
          if (index !== -1)
            runs.value[index] = run
        },
      })
    }
    finally {
      running.value = false
    }
  }

  function cancel() {
    cancelRequested.value = true
  }

  async function focusTab(tabId: number) {
    await getExtensionGlobal()?.tabs?.update(tabId, { active: true })
  }

  function clearRuns() {
    if (!running.value)
      runs.value = []
  }

  return {
    selectedIds,
    selectedPlatforms,
    loginStates,
    checkingLogin,
    runs,
    running,
    cancelRequested,
    setSelected,
    hasPermissions,
    requestPermissions,
    refreshLoginStates,
    publish,
    cancel,
    focusTab,
    clearRuns,
  }
})
