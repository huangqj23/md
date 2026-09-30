import type { AgentArticle, AgentErrorCode, AgentResult, FillReport } from './protocol'
import { describePage } from './dom'

export type AgentStep = (article: AgentArticle) => Promise<AgentResult>

export interface PlatformFiller {
  /** Steps by name; the editor always starts with `fill` unless the platform says otherwise. */
  steps: Record<string, AgentStep>
}

export function done(report: FillReport): AgentResult {
  return { kind: `done`, report }
}

export function fail(code: AgentErrorCode, message: string, report?: FillReport): AgentResult {
  return report ? { kind: `error`, code, message, report } : { kind: `error`, code, message }
}

export function navigate(url: string, step: string): AgentResult {
  return { kind: `navigate`, url, step }
}

/** `editor-not-found` with a page summary, so a report from the field is diagnosable. */
export function editorMissing(what: string): AgentResult {
  return fail(`editor-not-found`, `${what} not found — ${describePage()}`)
}
