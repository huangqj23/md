import type { AgentRequest, AgentResult } from '@/publish-agent/protocol'

/** Minimal typing of the WebExtension APIs the publisher uses (Chrome MV3 promise style). */
interface ExtTab {
  id?: number
  url?: string
  status?: string
}

interface ScriptInjection {
  target: { tabId: number }
  files?: string[]
  func?: (...args: never[]) => unknown
  args?: unknown[]
  world?: `MAIN` | `ISOLATED`
}

interface PermissionSet {
  permissions?: string[]
  origins?: string[]
}

export interface ExtensionGlobal {
  runtime?: {
    id?: string
    getManifest?: () => { permissions?: string[], optional_permissions?: string[] }
  }
  tabs?: {
    create: (props: { url: string, active?: boolean, openerTabId?: number }) => Promise<ExtTab>
    get: (tabId: number) => Promise<ExtTab>
    update: (tabId: number, props: { url?: string, active?: boolean }) => Promise<ExtTab | undefined>
    reload: (tabId: number) => Promise<void>
    getCurrent?: () => Promise<ExtTab | undefined>
    group?: (options: { tabIds: number[], groupId?: number }) => Promise<number>
  }
  tabGroups?: {
    update: (groupId: number, props: { title?: string, color?: string }) => Promise<unknown>
  }
  scripting?: {
    executeScript: (injection: ScriptInjection) => Promise<Array<{ result?: unknown }>>
  }
  permissions?: {
    contains: (permissions: PermissionSet) => Promise<boolean>
    request: (permissions: PermissionSet) => Promise<boolean>
  }
  cookies?: {
    get: (details: { url: string, name: string }) => Promise<{ value: string } | null>
  }
}

/** The extension namespace when this page runs inside the extension, else null. */
export function getExtensionGlobal(): ExtensionGlobal | null {
  const g = globalThis as { browser?: ExtensionGlobal, chrome?: ExtensionGlobal }
  if (g.browser?.runtime?.id)
    return g.browser
  if (g.chrome?.runtime?.id)
    return g.chrome
  return null
}

/** Built by WXT from `entrypoints/publish-agent.ts`. */
export const AGENT_SCRIPT_PATH = `/publish-agent.js`

/**
 * Runs in the platform page. `executeScript` serializes it by source, so it must
 * not reference imports or outer variables — hence the literal global name
 * (kept equal to `AGENT_GLOBAL` by a test).
 */
export function runAgentInPage(request: AgentRequest): Promise<AgentResult> {
  const agent = (window as unknown as { __mdPublishAgent?: { run: (r: AgentRequest) => Promise<AgentResult> } }).__mdPublishAgent
  if (!agent)
    return Promise.resolve({ kind: `error`, code: `exception`, message: `Publish agent is not loaded in this page` } as AgentResult)
  return agent.run(request)
}

export interface TabSnapshot {
  url?: string
  status?: string
}

/** What the publish runner needs from the browser; faked in tests. */
export interface PublishTabsApi {
  currentTabId: () => Promise<number | null>
  openTab: (url: string, openerTabId: number | null) => Promise<number>
  getTab: (tabId: number) => Promise<TabSnapshot | null>
  navigate: (tabId: number, url: string) => Promise<void>
  reloadTab: (tabId: number) => Promise<void>
  focusTab: (tabId: number) => Promise<void>
  injectAgent: (tabId: number) => Promise<void>
  runAgent: (tabId: number, request: AgentRequest) => Promise<AgentResult | undefined>
  /** Put tabs into one tab group; returns the group id, or null where unsupported. */
  groupTabs: (tabIds: number[], title: string, groupId: number | null) => Promise<number | null>
}

function withoutHash(url: string): string {
  const index = url.indexOf(`#`)
  return index === -1 ? url : url.slice(0, index)
}

export function createPublishTabsApi(ext: ExtensionGlobal): PublishTabsApi {
  const tabs = () => {
    if (!ext.tabs)
      throw new Error(`tabs API unavailable`)
    return ext.tabs
  }
  const scripting = () => {
    if (!ext.scripting)
      throw new Error(`scripting API unavailable; grant the extension's publish permissions`)
    return ext.scripting
  }

  return {
    async currentTabId() {
      const tab = await ext.tabs?.getCurrent?.()
      return tab?.id ?? null
    },
    async openTab(url, openerTabId) {
      const tab = await tabs().create(openerTabId === null ? { url, active: true } : { url, active: true, openerTabId })
      if (tab.id === undefined)
        throw new Error(`Browser did not return a tab id`)
      return tab.id
    },
    async getTab(tabId) {
      try {
        return await tabs().get(tabId)
      }
      catch {
        return null
      }
    },
    async navigate(tabId, url) {
      const current = await tabs().get(tabId)
      await tabs().update(tabId, { url })
      // A hash-only change does not reload; the platform must boot on the new route.
      if (current.url && current.url !== url && withoutHash(current.url) === withoutHash(url))
        await tabs().reload(tabId)
    },
    async reloadTab(tabId) {
      await tabs().reload(tabId)
    },
    async focusTab(tabId) {
      await tabs().update(tabId, { active: true })
    },
    async injectAgent(tabId) {
      await scripting().executeScript({ target: { tabId }, files: [AGENT_SCRIPT_PATH], world: `MAIN` })
    },
    async runAgent(tabId, request) {
      const [injection] = await scripting().executeScript({
        target: { tabId },
        func: runAgentInPage as (...args: never[]) => unknown,
        args: [request],
        world: `MAIN`,
      })
      return injection?.result as AgentResult | undefined
    },
    async groupTabs(tabIds, title, groupId) {
      if (!ext.tabs?.group)
        return null
      try {
        const id = await ext.tabs.group(groupId === null ? { tabIds } : { tabIds, groupId })
        if (groupId === null)
          await ext.tabGroups?.update(id, { title, color: `green` })
        return id
      }
      catch {
        return null
      }
    },
  }
}
