/**
 * Contract between the editor (extension page) and the publish agent, which is
 * injected into each platform's own editor tab (MAIN world). Everything here
 * must stay structured-clone friendly: it crosses `scripting.executeScript`.
 */

export const PUBLISH_PLATFORM_IDS = [
  `wechat`,
  `zhihu`,
  `csdn`,
  `juejin`,
  `toutiao`,
  `baijiahao`,
  `jianshu`,
  `bilibili`,
  `cnblogs`,
] as const

export type PublishPlatformId = typeof PUBLISH_PLATFORM_IDS[number]

/** Global the agent bundle registers on the platform page. */
export const AGENT_GLOBAL = `__mdPublishAgent`

/** Bump when the request/result shape changes, so a stale injection is detected. */
export const AGENT_PROTOCOL_VERSION = 1

export interface AgentArticle {
  title: string
  summary: string
  /** Markdown source, leading title heading removed. */
  markdown: string
  /** Unstyled semantic HTML; formulas degraded to their TeX source. */
  html: string
  /** Inline-styled HTML from the WeChat copy pipeline (empty when unavailable). */
  wechatHtml: string
  /** Visible text length of the rendered body, used to verify rich-text fills. */
  textLength: number
}

export interface AgentRequest {
  version: number
  platform: PublishPlatformId
  /** Platform-specific step, `fill` unless an earlier step asked to navigate. */
  step: string
  article: AgentArticle
}

export interface FillReport {
  titleFilled: boolean
  /** What the editor holds after the fill (text length, or source length for Markdown editors). */
  bodyLength: number
  /** What a complete fill should roughly hold, in the same unit as `bodyLength`. */
  expectedLength: number
  /** Which strategy ended up filling the body, for diagnostics. */
  method: string
  /** Whether a "save draft" action was triggered (or the platform autosaves). */
  draftSaved: boolean
  /** Embedded (data:) images the agent re-uploaded to the platform, when it does that. */
  images?: { total: number, failed: number }
}

export type AgentErrorCode
  = | `login-required`
    | `editor-not-found`
    | `fill-failed`
    | `unknown-step`
    | `version-mismatch`
    | `exception`

export type AgentResult
  = | { kind: `done`, report: FillReport }
    | { kind: `navigate`, url: string, step: string }
    | { kind: `error`, code: AgentErrorCode, message: string, report?: FillReport }

export interface PublishAgent {
  version: number
  run: (request: AgentRequest) => Promise<AgentResult>
}
