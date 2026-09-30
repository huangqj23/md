import type { PublishTabsApi, TabSnapshot } from './extension-api'
import type { PublishPlatform } from './platforms'
import type { AgentArticle, AgentErrorCode, AgentResult, FillReport, PublishPlatformId } from '@/publish-agent/protocol'
import { AGENT_PROTOCOL_VERSION } from '@/publish-agent/protocol'
import { fitTitle } from './article'

export type RunStatus
  = | `queued`
    | `opening`
    | `filling`
    | `success`
    | `warning`
    | `login-required`
    | `failed`
    | `cancelled`

export type RunErrorCode = AgentErrorCode | `page-timeout` | `agent-timeout` | `no-result` | `too-many-steps` | `browser-error`

/** Reasons a finished run still needs the user's attention. */
export type RunWarning = `title-truncated` | `title-not-filled` | `body-partial` | `images-not-uploaded` | `draft-not-saved`

export interface PlatformRun {
  id: PublishPlatformId
  status: RunStatus
  tabId?: number
  errorCode?: RunErrorCode
  /** Technical detail (English) for diagnostics. */
  detail?: string
  report?: FillReport
  warnings: RunWarning[]
}

export interface RunnerTiming {
  pageLoadTimeout: number
  stepTimeout: number
  /** Pause after load so single-page editors can boot before the agent looks. */
  settleDelay: number
  pollInterval: number
}

export const DEFAULT_TIMING: RunnerTiming = {
  pageLoadTimeout: 45000,
  stepTimeout: 90000,
  settleDelay: 1500,
  pollInterval: 250,
}

/** A body at least this share of the expected length counts as fully filled. */
export const BODY_COMPLETE_RATIO = 0.6
const MAX_STEPS = 3

export interface RunnerOptions {
  api: PublishTabsApi
  onUpdate: (run: PlatformRun) => void
  shouldCancel?: () => boolean
  timing?: Partial<RunnerTiming>
  sleep?: (ms: number) => Promise<void>
  /** Label for the tab group holding the opened platform tabs. */
  groupTitle?: string
}

const defaultSleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

class StepTimeoutError extends Error {}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new StepTimeoutError(`timed out after ${ms}ms`)), ms)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

export function classifyReport(report: FillReport, platform: Pick<PublishPlatform, `autosave`>, titleTruncated: boolean): { status: RunStatus, warnings: RunWarning[] } {
  const warnings: RunWarning[] = []
  if (titleTruncated)
    warnings.push(`title-truncated`)
  if (!report.titleFilled)
    warnings.push(`title-not-filled`)
  if (report.expectedLength > 0 && report.bodyLength / report.expectedLength < BODY_COMPLETE_RATIO)
    warnings.push(`body-partial`)
  if (report.images && report.images.failed > 0)
    warnings.push(`images-not-uploaded`)
  if (!report.draftSaved && !platform.autosave)
    warnings.push(`draft-not-saved`)
  return { status: warnings.length ? `warning` : `success`, warnings }
}

async function waitForTabLoad(
  api: PublishTabsApi,
  tabId: number,
  timing: RunnerTiming,
  sleep: (ms: number) => Promise<void>,
  afterNavigation: boolean,
): Promise<TabSnapshot | null> {
  const deadline = Date.now() + timing.pageLoadTimeout
  // Right after tabs.update() the tab can still report the previous page as complete.
  if (afterNavigation) {
    const loadingDeadline = Date.now() + Math.min(5000, timing.pageLoadTimeout)
    while (Date.now() < loadingDeadline) {
      const tab = await api.getTab(tabId)
      if (!tab)
        return null
      if (tab.status === `loading`)
        break
      await sleep(timing.pollInterval)
    }
  }
  while (Date.now() < deadline) {
    const tab = await api.getTab(tabId)
    if (!tab)
      return null
    if (tab.status === `complete`)
      return tab
    await sleep(timing.pollInterval)
  }
  return null
}

async function runPlatform(
  platform: PublishPlatform,
  article: AgentArticle,
  run: PlatformRun,
  update: () => void,
  ctx: { api: PublishTabsApi, timing: RunnerTiming, sleep: (ms: number) => Promise<void>, editorTabId: number | null, group: { id: number | null, title: string } },
) {
  const { api, timing, sleep } = ctx
  const fitted = fitTitle(article, platform.titleMaxLength)
  const finish = (status: RunStatus, extra: Partial<PlatformRun> = {}) => {
    Object.assign(run, { status }, extra)
    update()
  }

  run.status = `opening`
  update()
  const tabId = await api.openTab(platform.startUrl, ctx.editorTabId)
  run.tabId = tabId
  update()
  ctx.group.id = await api.groupTabs([tabId], ctx.group.title, ctx.group.id) ?? ctx.group.id

  /** No readable URL means the tab left the hosts we hold permission for: a login redirect. */
  const isLoginRedirect = (url: string | undefined) => !url || platform.loginUrlPattern.test(url)
  const loginRequired = (url: string | undefined) =>
    finish(`login-required`, { errorCode: `login-required`, detail: url ?? `redirected to another site` })
  /**
   * Single-page apps (Juejin, for one) redirect to their login route only after
   * the page reports "complete", so re-check before blaming the editor.
   */
  const failUnlessLoginRedirect = async (extra: Partial<PlatformRun>) => {
    const now = await api.getTab(tabId)
    if (now && isLoginRedirect(now.url))
      return loginRequired(now.url)
    return finish(`failed`, extra)
  }

  let step: string = platform.startStep
  for (let i = 0; i < MAX_STEPS; i++) {
    const tab = await waitForTabLoad(api, tabId, timing, sleep, i > 0)
    if (!tab)
      return finish(`failed`, { errorCode: `page-timeout`, detail: `${platform.startUrl} did not finish loading` })
    if (isLoginRedirect(tab.url))
      return loginRequired(tab.url)

    await sleep(timing.settleDelay)
    run.status = `filling`
    update()
    await api.focusTab(tabId)
    await api.injectAgent(tabId)

    let result: AgentResult | undefined
    try {
      result = await withTimeout(
        api.runAgent(tabId, { version: AGENT_PROTOCOL_VERSION, platform: platform.id, step, article: fitted.article }),
        timing.stepTimeout,
      )
    }
    catch (error) {
      // A timeout, or the page navigating away mid-step (which rejects executeScript).
      const detail = error instanceof StepTimeoutError ? `step "${step}" ${error.message}` : error instanceof Error ? error.message : String(error)
      return failUnlessLoginRedirect({ errorCode: error instanceof StepTimeoutError ? `agent-timeout` : `browser-error`, detail })
    }

    if (!result)
      return failUnlessLoginRedirect({ errorCode: `no-result`, detail: `step "${step}" returned nothing` })
    if (result.kind === `navigate`) {
      await api.navigate(tabId, result.url)
      step = result.step
      continue
    }
    if (result.kind === `error`) {
      if (result.code === `login-required`)
        return finish(`login-required`, { errorCode: result.code, detail: result.message })
      return failUnlessLoginRedirect({ errorCode: result.code, detail: result.message, report: result.report })
    }
    const { status, warnings } = classifyReport(result.report, platform, fitted.truncated)
    return finish(status, { report: result.report, warnings })
  }
  return finish(`failed`, { errorCode: `too-many-steps` })
}

/**
 * Fill every platform one after another, each in its own foreground tab (some
 * editors only initialise when visible). Tabs stay open for review; nothing is
 * published — at most a draft is saved.
 */
export async function runPublish(
  platforms: readonly PublishPlatform[],
  article: AgentArticle,
  options: RunnerOptions,
): Promise<PlatformRun[]> {
  const timing = { ...DEFAULT_TIMING, ...options.timing }
  const sleep = options.sleep ?? defaultSleep
  const { api } = options
  const runs: PlatformRun[] = platforms.map(p => ({ id: p.id, status: `queued`, warnings: [] }))
  runs.forEach(run => options.onUpdate({ ...run }))

  const editorTabId = await api.currentTabId().catch(() => null)
  const group = { id: null as number | null, title: options.groupTitle ?? `md` }

  for (const [index, platform] of platforms.entries()) {
    const run = runs[index]
    const update = () => options.onUpdate({ ...run, warnings: [...run.warnings] })
    if (options.shouldCancel?.()) {
      run.status = `cancelled`
      update()
      continue
    }
    try {
      await runPlatform(platform, article, run, update, { api, timing, sleep, editorTabId, group })
    }
    catch (error) {
      run.status = `failed`
      run.errorCode = `browser-error`
      run.detail = error instanceof Error ? error.message : String(error)
      update()
    }
  }

  if (editorTabId !== null)
    await api.focusTab(editorTabId).catch(() => {})
  return runs
}
