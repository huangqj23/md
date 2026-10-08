import type { AgentArticle } from '@/publish-agent/protocol'
import { countTextChars } from '@/publish-agent/dom'
import { FORMULA_TEX_ATTR } from '@/publish-agent/protocol'
import { parseFragment } from './article'

/** Draws one table; resolves to an image URL, or null to leave that table as it is. */
export type TableRenderer = (table: HTMLTableElement) => Promise<string | null>

/**
 * For editors without tables (Bilibili's column editor runs every cell of a pasted table together
 * into one paragraph): each table becomes a picture of itself, which the editor then uploads like any
 * other image. The renderer's scroll wrapper and its "scroll for more" hint go with the table.
 */
export async function withTablesAsImages(article: AgentArticle, render: TableRenderer = renderTableImage): Promise<AgentArticle> {
  const body = parseFragment(article.html)
  const tables = topLevelTables(body)
  const images: (string | null)[] = []
  for (const table of tables)
    images.push(await render(table))
  if (!images.some(Boolean))
    return article

  tables.forEach((table, index) => replaceTable(body, table, images[index]))
  return {
    ...article,
    html: body.innerHTML,
    // Editors fed the WeChat HTML (Baijiahao) get the same pictures, matched by position; both
    // come from one render, so the tables line up unless the two disagree on how many there are.
    wechatHtml: article.wechatHtml ? replaceTablesInOrder(article.wechatHtml, images) : article.wechatHtml,
    textLength: countTextChars(body.textContent),
  }
}

function topLevelTables(body: HTMLElement): HTMLTableElement[] {
  return Array.from(body.querySelectorAll(`table`)).filter(table => !table.parentElement?.closest(`table`))
}

function replaceTable(body: HTMLElement, table: HTMLTableElement, src: string | null) {
  if (!src)
    return
  const wrapper = table.parentElement
  const target = wrapper && wrapper !== body && wrapper.tagName === `SECTION` && wrapper.children.length === 1 ? wrapper : table
  const hint = target.nextElementSibling
  if (hint?.classList.contains(`table-scroll-hint`))
    hint.remove()
  const image = body.ownerDocument.createElement(`img`)
  image.setAttribute(`src`, src)
  target.replaceWith(image)
}

function replaceTablesInOrder(html: string, images: readonly (string | null)[]): string {
  const body = parseFragment(html)
  const tables = topLevelTables(body)
  if (tables.length !== images.length)
    return html
  tables.forEach((table, index) => replaceTable(body, table, images[index]))
  return body.innerHTML
}

const SHOT_CLASS = `md-publish-table-shot`
/** Layout width the table gets before it is drawn; wider tables keep their own width. */
const SHOT_WIDTH = 960

const SHOT_STYLE = `
.${SHOT_CLASS} { display: inline-block; padding: 12px; background: #fff; color: #1f2329;
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif; }
.${SHOT_CLASS} table { border-collapse: collapse; margin: 0; }
.${SHOT_CLASS} th, .${SHOT_CLASS} td { border: 1px solid #dcdfe6; padding: 6px 12px; vertical-align: top; overflow-wrap: anywhere; }
.${SHOT_CLASS} th { background: #f5f7fa; font-weight: 600; }
.${SHOT_CLASS} code { font-family: Menlo, Consolas, monospace; font-size: .9em; background: #f2f3f5; padding: 0 4px; border-radius: 3px; }
.${SHOT_CLASS} a { color: inherit; text-decoration: none; }
`

interface MathJaxLike {
  tex2svg?: (tex: string, options: { display: boolean }) => Element
}

/** Formulas in cells are `$…$` text by now; draw them again when the page has MathJax loaded. */
function drawFormulas(root: HTMLElement) {
  const mathJax = (window as unknown as { MathJax?: MathJaxLike }).MathJax
  if (typeof mathJax?.tex2svg !== `function`)
    return
  for (const holder of Array.from(root.querySelectorAll(`[${FORMULA_TEX_ATTR}]`))) {
    try {
      const svg = mathJax.tex2svg(holder.getAttribute(FORMULA_TEX_ATTR) ?? ``, { display: false }).querySelector(`svg`)
      if (svg)
        holder.replaceChildren(svg)
    }
    catch {}
  }
}

/** Draws a table off screen with plain styles: a PNG data URL, or null if the browser could not. */
export async function renderTableImage(table: HTMLTableElement): Promise<string | null> {
  const host = document.createElement(`div`)
  host.style.cssText = `position:fixed;left:-100000px;top:0;width:${SHOT_WIDTH}px;pointer-events:none;z-index:-1`
  const style = document.createElement(`style`)
  style.textContent = SHOT_STYLE
  const shot = document.createElement(`div`)
  shot.className = SHOT_CLASS
  shot.append(table.cloneNode(true))
  host.append(style, shot)
  document.body.append(host)
  try {
    drawFormulas(shot)
    const { toPng } = await import(`html-to-image`)
    return await toPng(shot, {
      backgroundColor: `#ffffff`,
      pixelRatio: 2,
      skipFonts: true,
      // A table wider than the layout width overflows its box; capture all of it.
      width: Math.ceil(Math.max(shot.scrollWidth, shot.offsetWidth)),
      height: Math.ceil(Math.max(shot.scrollHeight, shot.offsetHeight)),
    })
  }
  catch (error) {
    console.warn(`[publish] could not draw a table as an image`, error)
    return null
  }
  finally {
    host.remove()
  }
}
