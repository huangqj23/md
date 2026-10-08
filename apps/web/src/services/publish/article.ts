import type { AgentArticle } from '@/publish-agent/protocol'
import { generatePureHTML } from '@md/core/utils'
import { countTextChars } from '@/publish-agent/dom'
import { FORMULA_DISPLAY_ATTR, FORMULA_TEX_ATTR } from '@/publish-agent/protocol'

export const SUMMARY_MAX_LENGTH = 120

export interface ImageAudit {
  total: number
  /** http(s) URLs: platforms can fetch or re-host them. */
  remote: number
  /** data: URIs: many editors drop them. */
  embedded: number
  /** Relative, blob: or file paths: nothing outside this editor can load them. */
  local: number
}

function normalizeTitle(text: string): string {
  return text.replace(/\s+/g, ` `).trim()
}

/** Text of an ATX level-1 heading line (`# Title`, optional closing `#`s), else null. */
function parseH1(line: string): string | null {
  const opener = line.match(/^ {0,3}#[ \t]/)
  if (!opener)
    return null
  const text = line.slice(opener[0].length).trim().replace(/\s#+$/, ``).trim()
  return text ? normalizeTitle(text) : null
}

/** First ATX `# ` heading outside fenced code, or null. */
export function extractMarkdownTitle(markdown: string): string | null {
  let inFence = false
  for (const line of markdown.split(/\r?\n/)) {
    if (/^ {0,3}(?:```|~~~)/.test(line)) {
      inFence = !inFence
      continue
    }
    if (inFence)
      continue
    const title = parseH1(line)
    if (title)
      return title
  }
  return null
}

/**
 * Drop the H1 that repeats the title when it opens the document: every platform
 * has its own title field, so keeping it would show the title twice.
 */
export function stripLeadingTitle(markdown: string, title: string): string {
  const lines = markdown.split(/\r?\n/)
  let index = 0
  if (lines[0]?.trim() === `---`) {
    const end = lines.findIndex((line, i) => i > 0 && line.trim() === `---`)
    if (end > 0)
      index = end + 1
  }
  while (index < lines.length && lines[index].trim() === ``)
    index++
  const heading = lines[index] === undefined ? null : parseH1(lines[index])
  if (heading === null || heading !== normalizeTitle(title))
    return markdown
  lines.splice(index, 1)
  while (index < lines.length && lines[index].trim() === ``)
    lines.splice(index, 1)
  return lines.join(`\n`)
}

/** Same as `stripLeadingTitle`, for rendered HTML: only the first heading is considered. */
export function removeLeadingTitleHeading(root: ParentNode, title: string) {
  const heading = root.querySelector(`h1, h2, h3, h4, h5, h6`)
  if (heading?.tagName === `H1` && normalizeTitle(heading.textContent ?? ``) === normalizeTitle(title))
    heading.remove()
}

/** The TeX inside a formula's source: `$…$`, `$$…$$`, `\(…\)` or `\[…\]`. */
export function texOf(raw: string): string {
  const source = raw.trim()
  const match = source.match(/^\$\$([\s\S]*)\$\$$/) ?? source.match(/^\$([\s\S]*)\$$/)
    ?? source.match(/^\\\(([\s\S]*)\\\)$/) ?? source.match(/^\\\[([\s\S]*)\\\]$/)
  return (match ? match[1] : source).trim()
}

/**
 * MathJax output is SVG, which non-WeChat editors discard. Put the original `$…$` source back so
 * the formula survives as readable TeX, in an element that also keeps the bare TeX
 * (`FORMULA_TEX_ATTR`) for editors that can rebuild real formulas from it.
 */
export function degradeFormulas(root: ParentNode) {
  for (const formula of Array.from(root.querySelectorAll(`.katex-inline, .katex-block`))) {
    const raw = formula.getAttribute(`data-math-raw`) ?? ``
    const display = formula.classList.contains(`katex-block`)
    const holder = formula.ownerDocument.createElement(display ? `p` : `span`)
    holder.setAttribute(FORMULA_TEX_ATTR, texOf(raw))
    if (display)
      holder.setAttribute(FORMULA_DISPLAY_ATTR, ``)
    holder.textContent = raw
    formula.replaceWith(holder)
  }
}

export function auditImages(root: ParentNode): ImageAudit {
  const audit: ImageAudit = { total: 0, remote: 0, embedded: 0, local: 0 }
  for (const img of Array.from(root.querySelectorAll(`img`))) {
    const src = img.getAttribute(`src`) ?? ``
    audit.total++
    if (/^https?:\/\//i.test(src) || src.startsWith(`//`))
      audit.remote++
    else if (/^data:/i.test(src))
      audit.embedded++
    else
      audit.local++
  }
  return audit
}

/** First real paragraph, trimmed to the platforms' summary limit. */
export function extractSummary(root: ParentNode, maxLength = SUMMARY_MAX_LENGTH): string {
  for (const paragraph of Array.from(root.querySelectorAll(`p`))) {
    if (paragraph.closest(`blockquote, li, table, figure`))
      continue
    const text = (paragraph.textContent ?? ``).replace(/\s+/g, ` `).trim()
    if (countTextChars(text) >= 10)
      return Array.from(text).slice(0, maxLength).join(``)
  }
  return ``
}

export function parseFragment(html: string): HTMLElement {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, `text/html`)
  return doc.body
}

/** Clean, unstyled HTML for editors that keep structure but not styling. */
export function toPlatformHtml(html: string, title: string): { html: string, textLength: number } {
  const body = parseFragment(html)
  body.querySelectorAll(`style, script`).forEach(el => el.remove())
  removeLeadingTitleHeading(body, title)
  degradeFormulas(body)
  return { html: body.innerHTML.trim(), textLength: countTextChars(body.textContent) }
}

export function toWechatHtml(wechatHtml: string, title: string): string {
  if (!wechatHtml)
    return ``
  const body = parseFragment(wechatHtml)
  removeLeadingTitleHeading(body, title)
  return body.innerHTML
}

/**
 * Images only the WeChat article carries, i.e. the account's QR code: other platforms treat
 * off-site QR codes as promotion. watermark.py marks embedded ones `data-publish-only="wechat"`;
 * drafts still point at a file whose name contains `_wxonly`.
 */
const WECHAT_ONLY_IMAGES = `img[data-publish-only="wechat"], img[src*="_wxonly"]`
const WECHAT_ONLY_HTML_IMG = /<img\s[^>]*?data-publish-only="wechat"[^>]*>/gi
const WECHAT_ONLY_MD_IMG = /!\[[^\]]*\]\([^)\s]*_wxonly[^)\s]*\)/g

export function removeWechatOnlyImages(root: ParentNode) {
  for (const img of Array.from(root.querySelectorAll(WECHAT_ONLY_IMAGES))) {
    const holder = img.closest(`figure`) ?? img
    const parent = holder.parentElement
    holder.remove()
    if (parent?.tagName === `P` && !parent.textContent?.trim() && !parent.querySelector(`img, svg`))
      parent.remove()
  }
}

/** The article for every platform but WeChat: the same, minus the WeChat-only images. */
export function withoutWechatOnly(article: AgentArticle): AgentArticle {
  const strip = (html: string) => {
    const body = parseFragment(html)
    removeWechatOnlyImages(body)
    return body
  }
  const html = strip(article.html)
  return {
    ...article,
    markdown: article.markdown.replace(WECHAT_ONLY_HTML_IMG, ``).replace(WECHAT_ONLY_MD_IMG, ``).replace(/\n{3,}/g, `\n\n`).trim(),
    html: html.innerHTML.trim(),
    wechatHtml: article.wechatHtml ? strip(article.wechatHtml).innerHTML : ``,
    textLength: countTextChars(html.textContent),
  }
}

export interface ArticleSource {
  title: string
  summary: string
  markdown: string
  /** Output of the WeChat copy pipeline, or `` when the preview is not mounted. */
  wechatHtml: string
}

export async function buildAgentArticle(source: ArticleSource): Promise<AgentArticle> {
  const title = normalizeTitle(source.title)
  const markdown = stripLeadingTitle(source.markdown, title)
  const { html, textLength } = toPlatformHtml(await generatePureHTML(markdown), title)
  return {
    title,
    summary: source.summary.trim(),
    markdown,
    html,
    wechatHtml: toWechatHtml(source.wechatHtml, title),
    textLength,
  }
}

/** Clamp the title to a platform limit; reports whether anything was cut. */
export function fitTitle(article: AgentArticle, maxLength?: number): { article: AgentArticle, truncated: boolean } {
  const chars = Array.from(article.title)
  if (!maxLength || chars.length <= maxLength)
    return { article, truncated: false }
  return { article: { ...article, title: chars.slice(0, maxLength).join(``) }, truncated: true }
}
