/**
 * Bridge to the local AI daily pipeline (the Python project `ai-daily`) over native messaging.
 * The host is registered by `ai-daily install-host --extension-id <id>`; Chrome only lets the
 * extension IDs listed in the host manifest talk to it.
 */

import type { LlmReadiness } from './llm'

export const AI_DAILY_HOST = `com.hollis23.ai_daily`
export const NATIVE_PERMISSION = `nativeMessaging`

export interface NativeExtension {
  runtime?: {
    id?: string
    sendNativeMessage?: (host: string, message: object) => Promise<unknown>
    getManifest?: () => { permissions?: string[], optional_permissions?: string[] }
  }
  permissions?: {
    contains: (permissions: { permissions?: string[] }) => Promise<boolean>
    request: (permissions: { permissions?: string[] }) => Promise<boolean>
  }
}

export type JobState = `none` | `running` | `done` | `failed`

export interface JobStatus {
  state: JobState
  kind?: `run` | `publish`
  started?: string
  finished?: string | null
  returncode?: number | null
  log_tail?: string
  error?: string
}

export interface DraftFlag {
  section: string
  text: string
}

export interface DraftStatus {
  date: string
  article: null | {
    path: string
    modified: string
    title: string
    flags: DraftFlag[]
    opinion_written: boolean
    /** Blocking issues from `ai-daily publish`'s pre-flight check. */
    problems: string[]
  }
  review: boolean
  cover: boolean
  embed: null | { path: string, modified: string, stale: boolean }
  published: boolean
  job: JobStatus
  vault: string
  llm: LlmReadiness
}

export interface InboxList {
  count: number
  recent: string[]
}

/**
 * - `unsupported`: this build does not declare the permission (e.g. Firefox)
 * - `no-permission`: declared but not granted yet
 * - `not-installed` / `forbidden`: host missing, or registered for another extension ID
 * - `host-error`: the host crashed or replied with garbage
 * - `command`: the host ran and refused the request (message is user-facing)
 */
export type AiDailyErrorKind = `unsupported` | `no-permission` | `not-installed` | `forbidden` | `host-error` | `command`

export class AiDailyError extends Error {
  constructor(public kind: AiDailyErrorKind, message: string) {
    super(message)
    this.name = `AiDailyError`
  }
}

export function classifyNativeError(message: string): AiDailyErrorKind {
  if (/not found/i.test(message))
    return `not-installed`
  if (/forbidden/i.test(message))
    return `forbidden`
  return `host-error`
}

export function isAiDailyDeclared(ext: NativeExtension | null): boolean {
  const manifest = ext?.runtime?.getManifest?.()
  return [...(manifest?.permissions ?? []), ...(manifest?.optional_permissions ?? [])].includes(NATIVE_PERMISSION)
}

export async function hasNativePermission(ext: NativeExtension): Promise<boolean> {
  try {
    return (await ext.permissions?.contains({ permissions: [NATIVE_PERMISSION] })) ?? false
  }
  catch {
    return false
  }
}

/** Must run inside a click handler: browsers only show the prompt for a user gesture. */
export async function requestNativePermission(ext: NativeExtension): Promise<boolean> {
  return (await ext.permissions?.request({ permissions: [NATIVE_PERMISSION] })) ?? false
}

export async function callHost<T>(ext: NativeExtension | null, request: { cmd: string, [key: string]: unknown }): Promise<T> {
  if (!isAiDailyDeclared(ext))
    throw new AiDailyError(`unsupported`, `nativeMessaging is not declared in this build`)
  const send = ext?.runtime?.sendNativeMessage
  if (!send)
    throw new AiDailyError(`no-permission`, `nativeMessaging permission not granted`)
  let reply: unknown
  try {
    reply = await send(AI_DAILY_HOST, request)
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    throw new AiDailyError(classifyNativeError(message), message)
  }
  if (!reply || typeof reply !== `object` || !(`ok` in reply))
    throw new AiDailyError(`host-error`, `Unexpected reply from ${AI_DAILY_HOST}`)
  const body = reply as { ok: boolean, error?: string }
  if (!body.ok)
    throw new AiDailyError(`command`, body.error ?? `unknown error`)
  return reply as T
}

/** The embed version carries base64 images and can exceed the 1 MB message limit, so the host returns chunks. */
export async function readEmbed(ext: NativeExtension | null, date: string): Promise<{ title: string, content: string }> {
  const parts: string[] = []
  let title = ``
  let chunks = 1
  for (let chunk = 0; chunk < chunks; chunk++) {
    const reply = await callHost<{ title: string, chunks: number, content: string }>(ext, { cmd: `read_embed`, date, chunk })
    title = reply.title
    chunks = reply.chunks
    parts.push(reply.content)
  }
  return { title, content: parts.join(``) }
}

/** Loading a day again updates the post it created last time, unless that post has been deleted since. */
export function resolveLoadTarget(loadedPosts: Record<string, string>, date: string, postExists: (id: string) => boolean): { postId: string } | null {
  const id = loadedPosts[date]
  return id && postExists(id) ? { postId: id } : null
}

export function installCommand(extensionId: string): string {
  return `.venv\\Scripts\\ai-daily install-host --extension-id ${extensionId}`
}

/** Local date as YYYY-MM-DD (the pipeline names drafts by local date, not UTC). */
export function localDate(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, `0`)
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}
