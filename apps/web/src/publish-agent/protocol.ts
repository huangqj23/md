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

/**
 * In `AgentArticle.html` every formula is an element around its readable `$…$` source that carries
 * the bare TeX in this attribute, so editors with their own formula nodes can rebuild it. For a
 * platform with `displayFormulasAsImages`, a display formula's element holds a picture of it instead.
 */
export const FORMULA_TEX_ATTR = `data-tex`
/** Marks a display formula, which is a paragraph of its own. */
export const FORMULA_DISPLAY_ATTR = `data-tex-display`

export interface AgentArticle {
  title: string
  summary: string
  /** Markdown source, leading title heading removed. */
  markdown: string
  /** Unstyled semantic HTML; formulas degraded to their TeX source (see `FORMULA_TEX_ATTR`). */
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
  /**
   * Why the draft was not saved, when the platform would have refused it: `images`, some never
   * uploaded; `links`, more links than the account may have (see `links`); `unsaved`, the agent read
   * the draft back from the platform and its body was still empty.
   */
  draftBlockedBy?: `images` | `links` | `unsaved`
  /**
   * The platform caps links per article for this account (Jianshu: 2 for non-members). `unwrapped`
   * links went in as plain text to stay under `limit`; `remaining` is what the platform still counts.
   */
  links?: { limit: number, unwrapped: number, remaining: number }
  /**
   * Embedded (data:) images the agent re-uploaded to the platform, when it does that; `reasons`
   * holds the platform's distinct error messages for the ones that still failed.
   */
  images?: { total: number, failed: number, reasons?: string[] }
  /**
   * Formulas the platform will show as formulas (its own formula nodes, or its renderer);
   * `needsSetting` when an account setting keeps it from rendering any (Cnblogs' 启用数学公式支持).
   */
  formulas?: { total: number, placed: number, needsSetting?: boolean }
  /** Set when the preferred way in failed and a cruder one filled the body, e.g. `markdown-rejected`. */
  fallback?: string
}

export type AgentErrorCode
  = | `login-required`
    | `editor-not-found`
    | `fill-failed`
    /** The platform refuses an article this long. */
    | `too-long`
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
