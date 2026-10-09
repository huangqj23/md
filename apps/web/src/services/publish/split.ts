import type { AgentArticle } from '@/publish-agent/protocol'
import { generatePureHTML } from '@md/core/utils'
import { buildAgentArticle, toPlatformHtml } from './article'

/** A heading-delimited stretch of an article. */
export interface ArticleSection {
  /** Level of the heading that opens it; 0 for the text before the first heading. */
  level: number
  markdown: string
}

/** A footnote or link reference definition, carried by every part that uses it. */
export interface ReferenceDefinition {
  id: string
  footnote: boolean
  line: string
}

export interface ArticleOutline {
  sections: ArticleSection[]
  definitions: ReferenceDefinition[]
}

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/
const HEADING = /^ {0,3}(#{1,6})(?:[ \t]|$)/
const FOOTNOTE_DEFINITION = /^ {0,3}\[\^([^\]]+)\]:/
const LINK_DEFINITION = /^ {0,3}\[([^\]^][^\]]*)\]:\s*\S/

function isFenceClose(line: string, fence: string): boolean {
  const marker = fence[0] === `\`` ? `\`` : `~`
  return new RegExp(`^ {0,3}\\${marker}{${fence.length},}\\s*$`).test(line)
}

/** Cuts the article before each heading; code blocks and front matter stay intact. */
export function outlineArticle(markdown: string): ArticleOutline {
  const lines = markdown.split(/\r?\n/)
  const sections: ArticleSection[] = []
  const definitions: ReferenceDefinition[] = []
  let current: string[] = []
  let level = 0
  let fence: string | null = null

  const flush = () => {
    if (current.some(line => line.trim()))
      sections.push({ level, markdown: current.join(`\n`).trim() })
    current = []
  }

  let start = 0
  if (lines[0]?.trim() === `---`) {
    const end = lines.findIndex((line, index) => index > 0 && line.trim() === `---`)
    if (end > 0) {
      current.push(...lines.slice(0, end + 1))
      start = end + 1
    }
  }

  for (const line of lines.slice(start)) {
    if (fence) {
      current.push(line)
      if (isFenceClose(line, fence))
        fence = null
      continue
    }
    const fenceOpen = line.match(FENCE_OPEN)
    if (fenceOpen) {
      fence = fenceOpen[1]
      current.push(line)
      continue
    }
    const footnote = line.match(FOOTNOTE_DEFINITION)
    const link = footnote ? null : line.match(LINK_DEFINITION)
    if (footnote || link) {
      definitions.push({ id: (footnote ?? link)![1], footnote: Boolean(footnote), line: line.trim() })
      continue
    }
    const heading = line.match(HEADING)
    if (heading) {
      flush()
      level = heading[1].length
    }
    current.push(line)
  }
  flush()
  return { sections, definitions }
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, `\\$&`)
}

/** Definitions a part refers to: `[^id]` for footnotes, `[id]` for links. */
export function definitionsUsedIn(markdown: string, definitions: readonly ReferenceDefinition[]): ReferenceDefinition[] {
  return definitions.filter((definition) => {
    const reference = definition.footnote ? `[^${definition.id}]` : `[${definition.id}]`
    return new RegExp(escapeRegExp(reference), definition.footnote ? `` : `i`).test(markdown)
  })
}

/**
 * Groups sections into `parts` consecutive runs, cutting only before headings of level
 * `maxLevel` or higher, so that no run is longer than `cap` and the longest run is as
 * short as possible. Returns the index of the first section of each run, or null.
 */
export function chooseCuts(lengths: readonly number[], levels: readonly number[], parts: number, cap: number, maxLevel: number): number[] | null {
  const count = lengths.length
  if (parts < 1 || parts > count)
    return null
  const prefix = [0]
  for (const length of lengths)
    prefix.push(prefix[prefix.length - 1] + length)
  const canStart = (index: number) => index === 0 || (levels[index] >= 1 && levels[index] <= maxLevel)

  // best[k][i]: smallest possible longest run when sections [0, i) form k runs.
  const best = Array.from({ length: parts + 1 }, () => Array.from<number>({ length: count + 1 }).fill(Number.POSITIVE_INFINITY))
  const previous = Array.from({ length: parts + 1 }, () => Array.from<number>({ length: count + 1 }).fill(-1))
  best[0][0] = 0
  for (let k = 1; k <= parts; k++) {
    for (let end = 1; end <= count; end++) {
      if (end < count && !canStart(end))
        continue
      for (let begin = k - 1; begin < end; begin++) {
        if (!canStart(begin) || best[k - 1][begin] === Number.POSITIVE_INFINITY)
          continue
        const length = prefix[end] - prefix[begin]
        if (length > cap)
          continue
        const longest = Math.max(best[k - 1][begin], length)
        if (longest < best[k][end]) {
          best[k][end] = longest
          previous[k][end] = begin
        }
      }
    }
  }
  if (best[parts][count] === Number.POSITIVE_INFINITY)
    return null

  const starts: number[] = []
  let end = count
  for (let k = parts; k >= 1; k--) {
    const begin = previous[k][end]
    starts.unshift(begin)
    end = begin
  }
  return starts
}

export interface SplitOptions {
  /** Longest body a part may have, in the unit of `measure`. */
  limit: number
  /** Measures a piece of Markdown against `limit`; counts like `AgentArticle.textLength` when unset. */
  measure?: (markdown: string) => Promise<number>
  titleMaxLength?: number
  /** Gives up beyond this many parts. */
  maxParts?: number
  /** Title of part `index` (0-based) of `total`. */
  partTitle: (title: string, index: number, total: number) => string
  /** Line closing every part but the last; `title` is the next part's. */
  continuedIn: (title: string) => string
  /** Line opening every part but the first; `title` is the previous part's. */
  continuedFrom: (title: string) => string
}

export interface SplitDeps {
  /** Body length of a piece of Markdown, counted like `AgentArticle.textLength`. */
  measure: (markdown: string) => Promise<number>
  build: (input: { title: string, summary: string, markdown: string }) => Promise<AgentArticle>
}

const defaultDeps: SplitDeps = {
  measure: async markdown => toPlatformHtml(await generatePureHTML(markdown), ``).textLength,
  build: input => buildAgentArticle({ ...input, wechatHtml: `` }),
}

/**
 * What an image adds to stored HTML once on the platform's image host: its address and the box the
 * editor wraps it in (Jianshu's `image-package`), rather than an embedded image's data.
 */
const HOSTED_IMAGE_BYTES = 300

/** UTF-8 bytes of a piece of Markdown's HTML as an editor stores it, each image counted as hosted. */
export async function htmlBytes(markdown: string): Promise<number> {
  const { html } = toPlatformHtml(await generatePureHTML(markdown), ``)
  const images = html.match(/<img\b[^>]*>/gi) ?? []
  return new TextEncoder().encode(html.replace(/<img\b[^>]*>/gi, ``)).length + images.length * HOSTED_IMAGE_BYTES
}

/** Room for the "continued" lines and part titles inside each part. */
const PART_NOTE_ALLOWANCE = 200

/**
 * Splits an article that is over a platform's length limit into parts (上/下 for two)
 * at section headings, preferring the fewest parts, then the highest heading level,
 * then the most even split. Null when the article has no headings to split at, or a
 * single section is over the limit by itself.
 */
export async function splitArticle(
  article: Pick<AgentArticle, `title` | `summary` | `markdown`>,
  options: SplitOptions,
  deps: SplitDeps = defaultDeps,
): Promise<AgentArticle[] | null> {
  const { sections, definitions } = outlineArticle(article.markdown)
  if (sections.length < 2)
    return null

  const measure = options.measure ?? deps.measure
  const lengths: number[] = []
  for (const section of sections)
    lengths.push(await measure(section.markdown))
  const levels = sections.map(section => section.level)
  const headingLevels = [...new Set(levels.filter(level => level > 0))].sort((a, b) => a - b)
  const definitionsLength = definitions.length ? await measure(definitions.map(item => item.line).join(`\n\n`)) : 0
  const cap = options.limit - PART_NOTE_ALLOWANCE - definitionsLength
  const total = lengths.reduce((sum, length) => sum + length, 0)
  if (cap <= 0)
    return null

  const fitTitle = (index: number, total: number) => {
    const full = options.partTitle(article.title, index, total)
    const max = options.titleMaxLength
    if (!max || [...full].length <= max)
      return full
    const suffix = [...options.partTitle(``, index, total)].length
    return options.partTitle([...article.title].slice(0, Math.max(0, max - suffix)).join(``), index, total)
  }

  const build = async (starts: number[]) => {
    const groups = starts.map((start, index) => sections.slice(start, starts[index + 1] ?? sections.length))
    const titles = groups.map((_, index) => fitTitle(index, groups.length))
    const parts: AgentArticle[] = []
    for (const [index, group] of groups.entries()) {
      const body = group.map(section => section.markdown).join(`\n\n`)
      const blocks: string[] = []
      if (index > 0)
        blocks.push(options.continuedFrom(titles[index - 1]))
      blocks.push(body)
      if (index < groups.length - 1)
        blocks.push(options.continuedIn(titles[index + 1]))
      const used = definitionsUsedIn(body, definitions)
      if (used.length)
        blocks.push(used.map(item => item.line).join(`\n\n`))
      parts.push(await deps.build({ title: titles[index], summary: article.summary, markdown: blocks.join(`\n\n`) }))
    }
    return parts
  }

  const maxParts = Math.min(options.maxParts ?? 6, sections.length)
  for (let parts = Math.max(2, Math.ceil(total / cap)); parts <= maxParts; parts++) {
    for (const maxLevel of headingLevels) {
      const starts = chooseCuts(lengths, levels, parts, cap, maxLevel)
      if (!starts)
        continue
      const built = await build(starts)
      const sizes = options.measure
        ? await Promise.all(built.map(part => options.measure!(part.markdown)))
        : built.map(part => part.textLength)
      if (sizes.every(size => size <= options.limit))
        return built
    }
  }
  return null
}
