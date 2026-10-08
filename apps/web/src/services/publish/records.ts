import type { PartRun, PlatformRun, RunErrorCode, RunStatus, RunWarning } from './runner'
import type { FillReport, PublishPlatformId } from '@/publish-agent/protocol'

/** The per-platform log is for orientation, not an audit trail. */
export const HISTORY_LIMIT = 20

/**
 * Fingerprint of the article source (cyrb53). It only has to notice that the
 * article changed since a sync, so a fast non-cryptographic hash is enough.
 */
export function contentHash(text: string): string {
  let h1 = 0xDEADBEEF
  let h2 = 0x41C6CE57
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i)
    h1 = Math.imul(h1 ^ code, 2654435761)
    h2 = Math.imul(h2 ^ code, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507)
  h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507)
  h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36)
}

export type PublishEventKind = `synced` | `warning` | `failed` | `login-required` | `published` | `unpublished`

export interface PublishEvent {
  at: number
  kind: PublishEventKind
  /** Content version the event is about. */
  version?: string
  errorCode?: RunErrorCode
}

export interface PlatformRecord {
  /** Outcome of the latest finished attempt. */
  status: RunStatus
  at: number
  version: string
  report?: FillReport
  warnings: RunWarning[]
  errorCode?: RunErrorCode
  detail?: string
  /** Tab of the latest attempt. Tab ids are reused across browser sessions, so check the URL before trusting it. */
  tabId?: number
  /** Embedded or local images the article had; only WeChat re-uploads them. */
  unsupportedImages: number
  /** The latest draft this extension put on the platform. A failed attempt keeps the previous one. */
  draft?: { version: string, at: number }
  /** Set by the user once they published on the platform. */
  published?: { version: string, at: number, url?: string }
  /** The drafts of an article that went in as several parts. */
  parts?: PartRun[]
  history: PublishEvent[]
}

export interface PublishRecord {
  postId: string
  /** Title and summary of the last sync, reused when re-syncing from the publish center. */
  title: string
  summary: string
  /** False when they were the defaults derived from the article, which a re-sync derives again. */
  customTitle: boolean
  customSummary: boolean
  /** Content versions in the order they were first synced; the number shown is index + 1. */
  versions: string[]
  platforms: Partial<Record<PublishPlatformId, PlatformRecord>>
  updatedAt: number
}

export interface SyncStart {
  postId: string
  title: string
  summary: string
  customTitle: boolean
  customSummary: boolean
  version: string
  at: number
}

export function beginSync(record: PublishRecord | undefined, start: SyncStart): PublishRecord {
  const versions = record?.versions ?? []
  return {
    postId: start.postId,
    title: start.title,
    summary: start.summary,
    customTitle: start.customTitle,
    customSummary: start.customSummary,
    versions: versions.includes(start.version) ? versions : [...versions, start.version],
    platforms: record?.platforms ?? {},
    updatedAt: start.at,
  }
}

const EVENT_FOR_STATUS: Partial<Record<RunStatus, PublishEventKind>> = {
  'success': `synced`,
  'warning': `warning`,
  'failed': `failed`,
  'login-required': `login-required`,
}

/** A cancelled platform was never touched, so only these outcomes are recorded. */
export function isRecordedStatus(status: RunStatus): boolean {
  return EVENT_FOR_STATUS[status] !== undefined
}

function prepend(history: PublishEvent[] | undefined, event: PublishEvent): PublishEvent[] {
  return [event, ...(history ?? [])].slice(0, HISTORY_LIMIT)
}

function withPlatform(record: PublishRecord, id: PublishPlatformId, platform: PlatformRecord, at: number): PublishRecord {
  return { ...record, platforms: { ...record.platforms, [id]: platform }, updatedAt: at }
}

export interface RunContext {
  version: string
  at: number
  unsupportedImages: number
}

export function applyRun(record: PublishRecord, run: PlatformRun, context: RunContext): PublishRecord {
  const kind = EVENT_FOR_STATUS[run.status]
  if (!kind)
    return record
  const previous = record.platforms[run.id]
  const isFilled = (status: RunStatus) => status === `success` || status === `warning`
  // A split article keeps the parts that did go in, even when another part failed.
  const filled = isFilled(run.status) || Boolean(run.parts?.some(part => isFilled(part.status)))
  const event: PublishEvent = run.errorCode
    ? { at: context.at, kind, version: context.version, errorCode: run.errorCode }
    : { at: context.at, kind, version: context.version }
  const next: PlatformRecord = {
    status: run.status,
    at: context.at,
    version: context.version,
    report: run.report,
    warnings: [...run.warnings],
    errorCode: run.errorCode,
    detail: run.detail,
    tabId: run.tabId,
    unsupportedImages: context.unsupportedImages,
    draft: filled ? { version: context.version, at: context.at } : previous?.draft,
    published: previous?.published,
    parts: run.parts?.map(part => ({ ...part, warnings: [...part.warnings] })),
    history: prepend(previous?.history, event),
  }
  return withPlatform(record, run.id, next, context.at)
}

/** Records what the user published: the draft the extension last filled. */
export function markPublished(record: PublishRecord, id: PublishPlatformId, at: number, url?: string): PublishRecord {
  const platform = record.platforms[id]
  if (!platform?.draft)
    return record
  const { version } = platform.draft
  const published = url ? { version, at, url } : { version, at }
  return withPlatform(record, id, { ...platform, published, history: prepend(platform.history, { at, kind: `published`, version }) }, at)
}

export function unmarkPublished(record: PublishRecord, id: PublishPlatformId, at: number): PublishRecord {
  const platform = record.platforms[id]
  if (!platform?.published)
    return record
  const { published: _, ...rest } = platform
  return withPlatform(record, id, { ...rest, history: prepend(platform.history, { at, kind: `unpublished` }) }, at)
}

export function versionNumber(record: PublishRecord | undefined, version: string | undefined): number | null {
  if (!record || !version)
    return null
  const index = record.versions.indexOf(version)
  return index === -1 ? null : index + 1
}

/** Only http(s) links are kept: the URL ends up in an `href`. */
export function normalizePublishedUrl(input: string): string | null {
  const value = input.trim()
  if (!value)
    return null
  try {
    const url = new URL(value)
    return url.protocol === `http:` || url.protocol === `https:` ? url.href : null
  }
  catch {
    return null
  }
}
