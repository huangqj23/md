import type { PublishPlatform } from './platforms'
import type { PlatformRecord, PublishRecord } from './records'
import type { PlatformRun, RunErrorCode, RunStatus } from './runner'
import { versionNumber } from './records'

/** What a platform row shows; a live run wins over the stored record. */
export type PlatformState = `queued` | `syncing` | `draft` | `attention` | `failed` | `login-required` | `published` | `idle`

export type CheckLevel = `ok` | `warn` | `bad`

export type CheckKey
  = | `titleFilled`
    | `titleNotFilled`
    | `titleTruncated`
    | `bodyFilled`
    | `bodyPartial`
    | `imagesUploaded`
    | `imagesNotUploaded`
    | `imagesUnsupported`
    | `draftSaved`
    | `autosave`
    | `draftNotSaved`
    | `error`

export interface Check {
  level: CheckLevel
  key: CheckKey
  params?: Record<string, string | number>
}

export interface PlatformView {
  platform: PublishPlatform
  state: PlatformState
  record?: PlatformRecord
  live?: PlatformRun
  /** Result of the latest finished attempt. */
  checks: Check[]
  /** The draft or the published article on the platform is older than the article now. */
  outdated: boolean
  /** Version number of what the platform holds (published article, else draft). */
  heldVersion: number | null
}

const LIVE_STATUSES: RunStatus[] = [`queued`, `opening`, `filling`]

export function isLiveStatus(status: RunStatus): boolean {
  return LIVE_STATUSES.includes(status)
}

export function errorMessageKey(code?: RunErrorCode): string {
  switch (code) {
    case `login-required`:
      return `publish.errors.loginRequired`
    case `editor-not-found`:
      return `publish.errors.editorNotFound`
    case `fill-failed`:
      return `publish.errors.fillFailed`
    case `page-timeout`:
      return `publish.errors.pageTimeout`
    case `page-error`:
      return `publish.errors.pageError`
    case `agent-timeout`:
      return `publish.errors.agentTimeout`
    default:
      return `publish.errors.generic`
  }
}

export function recordChecks(platform: Pick<PublishPlatform, `autosave` | `titleMaxLength`>, record: PlatformRecord): Check[] {
  if (record.status === `failed` || record.status === `login-required`)
    return [{ level: `bad`, key: `error`, params: { code: record.errorCode ?? `` } }]

  const checks: Check[] = []
  const { report, warnings } = record
  if (warnings.includes(`title-not-filled`))
    checks.push({ level: `warn`, key: `titleNotFilled` })
  else if (warnings.includes(`title-truncated`))
    checks.push({ level: `warn`, key: `titleTruncated`, params: { limit: platform.titleMaxLength ?? 0 } })
  else
    checks.push({ level: `ok`, key: `titleFilled` })

  if (!report)
    return checks

  const percent = report.expectedLength > 0
    ? Math.min(100, Math.round(report.bodyLength / report.expectedLength * 100))
    : 100
  checks.push(warnings.includes(`body-partial`)
    ? { level: `warn`, key: `bodyPartial`, params: { percent } }
    : { level: `ok`, key: `bodyFilled`, params: { percent } })

  if (report.images) {
    checks.push(report.images.failed > 0
      ? { level: `warn`, key: `imagesNotUploaded`, params: { ...report.images } }
      : { level: `ok`, key: `imagesUploaded`, params: { total: report.images.total } })
  }
  else if (record.unsupportedImages > 0) {
    checks.push({ level: `warn`, key: `imagesUnsupported`, params: { count: record.unsupportedImages } })
  }

  if (report.draftSaved)
    checks.push({ level: `ok`, key: `draftSaved` })
  else if (platform.autosave)
    checks.push({ level: `ok`, key: `autosave` })
  else
    checks.push({ level: `warn`, key: `draftNotSaved` })
  return checks
}

function stateOf(record: PlatformRecord | undefined, checks: Check[], live: PlatformRun | undefined): PlatformState {
  if (live && isLiveStatus(live.status))
    return live.status === `queued` ? `queued` : `syncing`
  if (!record)
    return `idle`
  if (record.published)
    return `published`
  switch (record.status) {
    case `success`:
    case `warning`:
      return checks.some(check => check.level === `warn`) ? `attention` : `draft`
    case `login-required`:
      return `login-required`
    case `failed`:
      return `failed`
    default:
      return `idle`
  }
}

/** `currentVersion` is the hash of the article as it is now; `liveRuns` belong to a sync in progress. */
export function buildPlatformViews(
  platforms: readonly PublishPlatform[],
  record: PublishRecord | undefined,
  currentVersion: string,
  liveRuns: readonly PlatformRun[] = [],
): PlatformView[] {
  return platforms.map((platform) => {
    const platformRecord = record?.platforms[platform.id]
    const live = liveRuns.find(run => run.id === platform.id)
    const checks = platformRecord ? recordChecks(platform, platformRecord) : []
    const state = stateOf(platformRecord, checks, live)
    const held = platformRecord?.published?.version
      ?? (state === `draft` || state === `attention` ? platformRecord?.draft?.version : undefined)
    return {
      platform,
      state,
      record: platformRecord,
      live,
      checks,
      outdated: held !== undefined && held !== currentVersion,
      heldVersion: versionNumber(record, held),
    }
  })
}

export type CenterFilter = `all` | `todo` | `pending` | `published` | `idle`

export const CENTER_FILTERS: readonly CenterFilter[] = [`all`, `todo`, `pending`, `published`, `idle`]

export function matchesFilter(view: PlatformView, filter: CenterFilter): boolean {
  switch (filter) {
    case `todo`:
      return view.state === `failed` || view.state === `login-required` || view.state === `attention` || view.outdated
    case `pending`:
      return view.state === `draft` || view.state === `attention`
    case `published`:
      return view.state === `published`
    case `idle`:
      return view.state === `idle`
    default:
      return true
  }
}

/** Today's events show the time only; older ones get the date too. */
export function formatPublishTime(at: number, locale: string, now: number = Date.now()): string {
  const date = new Date(at)
  if (date.toDateString() === new Date(now).toDateString())
    return date.toLocaleTimeString(locale, { hour: `2-digit`, minute: `2-digit` })
  return date.toLocaleString(locale, { month: `short`, day: `numeric`, hour: `2-digit`, minute: `2-digit` })
}

interface ListFormatter {
  format: (items: string[]) => string
}

/** `Intl.ListFormat` is newer than the ES2020 lib this app compiles against. */
type ListFormatConstructor = new (locale: string, options: { style: `long`, type: `conjunction` }) => ListFormatter

/** "CSDN、掘金和博客园" / "CSDN, Juejin, and Cnblogs". */
export function formatNameList(names: string[], locale: string): string {
  const ListFormat = (Intl as unknown as { ListFormat?: ListFormatConstructor }).ListFormat
  return ListFormat ? new ListFormat(locale, { style: `long`, type: `conjunction` }).format(names) : names.join(`, `)
}

/** Drafts that can simply be synced again, and published articles that must be updated by hand. */
export function outdatedViews(views: readonly PlatformView[]): { drafts: PlatformView[], published: PlatformView[] } {
  return {
    drafts: views.filter(view => view.outdated && (view.state === `draft` || view.state === `attention`)),
    published: views.filter(view => view.outdated && view.state === `published`),
  }
}
