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

export type RunErrorCode = AgentErrorCode | `page-timeout` | `page-error` | `agent-timeout` | `no-result` | `too-many-steps` | `browser-error`

/**
 * Chrome refuses to inject into a tab showing its own "this page failed to load" screen
 * ("Frame with ID 0 is showing error page") — a network or proxy failure, not the platform.
 */
const ERROR_PAGE_PATTERN = /showing error page/i

function isErrorPage(error: unknown): boolean {
  return ERROR_PAGE_PATTERN.test(error instanceof Error ? error.message : String(error))
}

/** Reasons a finished run still needs the user's attention. */
export type RunWarning = `title-truncated` | `title-not-filled` | `body-partial` | `images-not-uploaded` | `links-unwrapped` | `draft-not-saved` | `fill-fallback`

/** One part of an article that went to a platform in several parts. */
export interface PartRun {
  title: string
  status: RunStatus
  tabId?: number
  errorCode?: RunErrorCode
  detail?: string
  report?: FillReport
  warnings: RunWarning[]
}

export interface PlatformRun {
  id: PublishPlatformId
  status: RunStatus
  tabId?: number
  errorCode?: RunErrorCode
  /** Technical detail (English) for diagnostics. */
  detail?: string
  report?: FillReport
  warnings: RunWarning[]
  /** Set when the article was over the platform's length limit and went in as several drafts. */
  parts?: PartRun[]
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
  /** Platforms that get the article in parts (each its own draft) instead of whole. */
  parts?: Partial<Record<PublishPlatformId, AgentArticle[]>>
  /** Platforms that get their own version of the article, e.g. WeChat keeps the account's QR code. */
  articles?: Partial<Record<PublishPlatformId, AgentArticle>>
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
  if (report.links && report.links.unwrapped > 0)
    warnings.push(`links-unwrapped`)
  // An autosaving platform that refused the draft did not save it either.
  if (!report.draftSaved && (!platform.autosave || report.draftBlockedBy))
    warnings.push(`draft-not-saved`)
  if (report.fallback)
    warnings.push(`fill-fallback`)
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
  const pageError = async () => {
    const now = await api.getTab(tabId)
    return finish(`failed`, { errorCode: `page-error`, detail: `${now?.url ?? platform.startUrl} failed to load` })
  }
  /** A page that failed to load (flaky network or proxy) gets one reload before giving up. */
  const injectAgent = async (): Promise<boolean> => {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await api.injectAgent(tabId)
        return true
      }
      catch (error) {
        if (!isErrorPage(error) || attempt > 0)
          throw error
      }
      await api.reloadTab(tabId)
      if (!(await waitForTabLoad(api, tabId, timing, sleep, true)))
        break
      await sleep(timing.settleDelay)
    }
    return false
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
    try {
      if (!(await injectAgent()))
        return pageError()
    }
    catch (error) {
      if (isErrorPage(error))
        return pageError()
      throw error
    }

    let result: AgentResult | undefined
    try {
      result = await withTimeout(
        api.runAgent(tabId, { version: AGENT_PROTOCOL_VERSION, platform: platform.id, step, article: fitted.article }),
        Math.max(timing.stepTimeout, platform.stepTimeout ?? 0),
      )
    }
    catch (error) {
      if (isErrorPage(error))
        return pageError()
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

const LIVE_STATUSES: RunStatus[] = [`queued`, `opening`, `filling`]

/** Overall result of a platform that got the article in parts. */
export function combineParts(parts: readonly PartRun[]): Pick<PlatformRun, `status` | `errorCode` | `detail` | `report` | `warnings` | `tabId`> {
  const reports = parts.map(part => part.report).filter((report): report is FillReport => Boolean(report))
  const sum = (pick: (report: FillReport) => number) => reports.reduce((total, report) => total + pick(report), 0)
  const images = reports.filter(report => report.images)
  const reasons = [...new Set(images.flatMap(item => item.images?.reasons ?? []))]
  const formulas = reports.filter(report => report.formulas)
  const links = reports.filter(report => report.links)
  const blockedBy = reports.find(report => report.draftBlockedBy)?.draftBlockedBy
  const report: FillReport | undefined = reports.length
    ? {
        titleFilled: reports.every(item => item.titleFilled),
        bodyLength: sum(item => item.bodyLength),
        expectedLength: sum(item => item.expectedLength),
        method: [...new Set(reports.map(item => item.method))].join(`+`),
        draftSaved: reports.every(item => item.draftSaved),
        ...(images.length
          ? { images: { total: sum(item => item.images?.total ?? 0), failed: sum(item => item.images?.failed ?? 0), ...(reasons.length ? { reasons } : {}) } }
          : {}),
        ...(formulas.length
          ? { formulas: {
              total: sum(item => item.formulas?.total ?? 0),
              placed: sum(item => item.formulas?.placed ?? 0),
              ...(formulas.some(item => item.formulas?.needsSetting) ? { needsSetting: true } : {}),
            } }
          : {}),
        // Each part is a draft of its own, so the limit applies per part.
        ...(links.length
          ? { links: {
              limit: links[0].links!.limit,
              unwrapped: sum(item => item.links?.unwrapped ?? 0),
              remaining: Math.max(...links.map(item => item.links!.remaining)),
            } }
          : {}),
        ...(blockedBy ? { draftBlockedBy: blockedBy } : {}),
        ...(reports.some(item => item.fallback) ? { fallback: reports.find(item => item.fallback)!.fallback } : {}),
      }
    : undefined
  const warnings = [...new Set(parts.flatMap(part => part.warnings))]
  const tabId = parts.find(part => part.tabId !== undefined)?.tabId
  const broken = parts.findIndex(part => part.status !== `success` && part.status !== `warning`)
  if (broken === -1)
    return { status: warnings.length ? `warning` : `success`, report, warnings, tabId }
  const part = parts[broken]
  return {
    status: parts.some(item => item.status === `login-required`) ? `login-required` : `failed`,
    errorCode: part.errorCode,
    detail: `Part ${broken + 1}/${parts.length}: ${part.detail ?? (part.status === `cancelled` ? `not synced` : part.status)}`,
    report,
    warnings,
    tabId,
  }
}

/**
 * Fills the parts one after another, each as its own draft. The platform run stays
 * live until the last part is done; a login problem skips the remaining parts.
 */
async function runPlatformInParts(
  platform: PublishPlatform,
  articles: readonly AgentArticle[],
  run: PlatformRun,
  update: () => void,
  ctx: Parameters<typeof runPlatform>[4],
  shouldCancel?: () => boolean,
) {
  const parts: PartRun[] = articles.map(article => ({ title: article.title, status: `queued`, warnings: [] }))
  run.parts = parts
  for (const [index, article] of articles.entries()) {
    const part = parts[index]
    if (shouldCancel?.() || parts.some(item => item.status === `login-required`)) {
      part.status = `cancelled`
      continue
    }
    const partRun: PlatformRun = { id: platform.id, status: `queued`, warnings: [] }
    const follow = () => {
      if (LIVE_STATUSES.includes(partRun.status))
        run.status = partRun.status
      part.tabId = partRun.tabId
      run.tabId ??= partRun.tabId
      update()
    }
    try {
      await runPlatform(platform, article, partRun, follow, ctx)
    }
    catch (error) {
      Object.assign(partRun, { status: `failed`, errorCode: `browser-error`, detail: error instanceof Error ? error.message : String(error) })
    }
    Object.assign(part, {
      status: partRun.status,
      tabId: partRun.tabId,
      errorCode: partRun.errorCode,
      detail: partRun.detail,
      report: partRun.report,
      warnings: partRun.warnings,
    })
  }
  Object.assign(run, combineParts(parts))
  update()
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
    const update = () => options.onUpdate({
      ...run,
      warnings: [...run.warnings],
      ...(run.parts ? { parts: run.parts.map(part => ({ ...part, warnings: [...part.warnings] })) } : {}),
    })
    if (options.shouldCancel?.()) {
      run.status = `cancelled`
      update()
      continue
    }
    const ctx = { api, timing, sleep, editorTabId, group }
    const parts = options.parts?.[platform.id]
    try {
      if (parts && parts.length > 1)
        await runPlatformInParts(platform, parts, run, update, ctx, options.shouldCancel)
      else
        await runPlatform(platform, options.articles?.[platform.id] ?? article, run, update, ctx)
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
