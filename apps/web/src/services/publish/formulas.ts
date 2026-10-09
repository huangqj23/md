import type { AgentArticle } from '@/publish-agent/protocol'
import { FORMULA_DISPLAY_ATTR, FORMULA_TEX_ATTR } from '@/publish-agent/protocol'
import { parseFragment, texOf } from './article'
import { texToText } from './tex-text'

/** A display formula drawn as a picture: its URL and the size it should show at, in CSS pixels. */
export interface FormulaImage {
  src: string
  width: number
  height: number
}

/** Draws one MathJax SVG; resolves to the picture, or null to fall back to the formula's TeX. */
export type FormulaRenderer = (svg: SVGSVGElement) => Promise<FormulaImage | null>

/**
 * For editors fed the WeChat HTML that strip SVG (Baijiahao's UEditor leaves a blank where each
 * formula was) and that turn every image into its own captioned block, which splits a sentence or
 * list item around an inline picture:
 * - display formulas become pictures of themselves, uploaded by the agent like any embedded image;
 * - inline formulas become plain text (`3.8 × 10²⁵`, see `texToText`).
 * Either falls back to its `$…$` source when it cannot be drawn or written as text.
 */
export async function withPlainFormulas(article: AgentArticle, render: FormulaRenderer = renderFormulaImage): Promise<AgentArticle> {
  if (!article.wechatHtml)
    return article
  const body = parseFragment(article.wechatHtml)
  const formulas = Array.from(body.querySelectorAll<HTMLElement>(`.katex-inline, .katex-block`))
  if (!formulas.length)
    return article

  // Papers repeat the same formulas; draw each distinct one once.
  const drawn = new Map<string, Promise<FormulaImage | null>>()
  for (const formula of formulas) {
    const raw = formula.getAttribute(`data-math-raw`) ?? formula.textContent ?? ``
    if (formula.classList.contains(`katex-inline`)) {
      // No-break spaces keep a short formula on one line, as it was when typeset.
      const text = texToText(texOf(raw))?.replace(/ /g, `\u00A0`)
      formula.replaceWith(body.ownerDocument.createTextNode(text ?? raw))
      continue
    }
    const svg = formula.querySelector(`svg`)
    let image: FormulaImage | null = null
    if (svg) {
      const key = svg.outerHTML
      if (!drawn.has(key))
        drawn.set(key, render(svg).catch(() => null))
      image = await drawn.get(key)!
    }
    formula.replaceWith(displayFormula(body.ownerDocument, raw, image))
  }
  return { ...article, wechatHtml: body.innerHTML }
}

function displayFormula(doc: Document, raw: string, image: FormulaImage | null): HTMLElement {
  const paragraph = doc.createElement(`p`)
  paragraph.setAttribute(`style`, `text-align: center;`)
  if (!image) {
    paragraph.textContent = raw
    return paragraph
  }
  paragraph.append(formulaPicture(doc, raw, image))
  return paragraph
}

function formulaPicture(doc: Document, raw: string, image: FormulaImage): HTMLImageElement {
  const img = doc.createElement(`img`)
  img.setAttribute(`src`, image.src)
  img.setAttribute(`alt`, raw)
  img.setAttribute(`width`, String(Math.round(image.width)))
  img.setAttribute(`height`, String(Math.round(image.height)))
  img.setAttribute(`style`, `width: ${image.width}px; height: ${image.height}px; max-width: 100%;`)
  return img
}

interface MathJaxLike {
  tex2svg?: (tex: string, options: { display: boolean }) => Element
  texReset?: () => void
}

/** A display formula typeset again, for when the WeChat HTML (and the SVG in it) is not at hand. */
function typesetDisplay(tex: string): SVGSVGElement | null {
  const mathJax = (window as unknown as { MathJax?: MathJaxLike }).MathJax
  if (typeof mathJax?.tex2svg !== `function`)
    return null
  try {
    mathJax.texReset?.()
    return mathJax.tex2svg(tex, { display: true }).querySelector(`svg`)
  }
  catch {
    return null
  }
}

/**
 * For editors fed `article.html` that have no formulas of their own (Jianshu's): each display
 * formula becomes a picture of itself, drawn from its SVG in the WeChat HTML (or typeset again
 * when that is missing). The holder stays around the picture with its TeX (`FORMULA_TEX_ATTR`), so
 * the agent can still write the formula as text if the picture does not reach the platform. Inline
 * formulas are left to the agent.
 */
export async function withDisplayFormulaImages(article: AgentArticle, render: FormulaRenderer = renderFormulaImage): Promise<AgentArticle> {
  const body = parseFragment(article.html)
  const holders = Array.from(body.querySelectorAll<HTMLElement>(`[${FORMULA_DISPLAY_ATTR}]`))
  if (!holders.length)
    return article

  // Both bodies come from the same Markdown, so a formula's TeX finds its SVG.
  const typeset = new Map<string, SVGSVGElement>()
  const wechat = article.wechatHtml ? Array.from(parseFragment(article.wechatHtml).querySelectorAll(`.katex-block`)) : []
  for (const formula of wechat) {
    const svg = formula.querySelector(`svg`)
    const tex = texOf(formula.getAttribute(`data-math-raw`) ?? ``)
    if (svg && tex && !typeset.has(tex))
      typeset.set(tex, svg)
  }

  // Papers repeat the same formulas; draw each distinct one once.
  const drawn = new Map<string, Promise<FormulaImage | null>>()
  let changed = false
  for (const holder of holders) {
    const tex = holder.getAttribute(FORMULA_TEX_ATTR) ?? ``
    if (!drawn.has(tex)) {
      const svg = typeset.get(tex) ?? typesetDisplay(tex)
      drawn.set(tex, svg ? render(svg).catch(() => null) : Promise.resolve(null))
    }
    const image = await drawn.get(tex)!
    if (!image)
      continue
    holder.setAttribute(`style`, `text-align: center;`)
    holder.replaceChildren(formulaPicture(body.ownerDocument, holder.textContent ?? ``, image))
    changed = true
  }
  return changed ? { ...article, html: body.innerHTML } : article
}

/** How a formula is drawn. */
export interface FormulaDrawing {
  /** Image pixels per CSS pixel. */
  pixelRatio: number
  /** Text size the formula is laid out at. */
  fontSize: string
  /** Fill behind the formula; transparent when unset. */
  background?: string
  /** Room around the formula, in CSS pixels, so no stroke touches the picture's edge. */
  padding?: number
}

/** Formula ink on the platform's white page (the WeChat HTML leaves it as `currentColor`). */
const FORMULA_COLOR = `#333333`
/**
 * At md's body text size, three times sharper than its CSS size for high-DPI screens: for editors
 * that size a picture by its width and height.
 */
const FORMULA_DRAWING: FormulaDrawing = { pixelRatio: 3, fontSize: `16px` }

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error(`formula SVG did not load`))
    image.src = src
  })
}

/** Draws a MathJax SVG on a canvas at its laid-out size: a PNG data URL, or null if the browser could not. */
export async function renderFormulaImage(svg: SVGSVGElement, drawing: FormulaDrawing = FORMULA_DRAWING): Promise<FormulaImage | null> {
  const host = document.createElement(`div`)
  host.style.cssText = `position:fixed;left:-100000px;top:0;font-size:${drawing.fontSize};line-height:normal;pointer-events:none;z-index:-1`
  const clone = svg.cloneNode(true) as SVGSVGElement
  host.append(clone)
  document.body.append(host)
  try {
    const { width, height } = clone.getBoundingClientRect()
    if (!width || !height)
      return null
    clone.setAttribute(`width`, `${width}`)
    clone.setAttribute(`height`, `${height}`)
    clone.removeAttribute(`style`)
    clone.style.color = FORMULA_COLOR
    const markup = new XMLSerializer().serializeToString(clone)
    const image = await loadImage(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`)

    const { pixelRatio, background, padding = 0 } = drawing
    const canvas = document.createElement(`canvas`)
    canvas.width = Math.ceil((width + 2 * padding) * pixelRatio)
    canvas.height = Math.ceil((height + 2 * padding) * pixelRatio)
    const context = canvas.getContext(`2d`)!
    if (background) {
      context.fillStyle = background
      context.fillRect(0, 0, canvas.width, canvas.height)
    }
    const inset = padding * pixelRatio
    context.drawImage(image, inset, inset, canvas.width - 2 * inset, canvas.height - 2 * inset)
    return {
      src: canvas.toDataURL(`image/png`),
      width: Math.round((width + 2 * padding) * 100) / 100,
      height: Math.round((height + 2 * padding) * 100) / 100,
    }
  }
  catch (error) {
    console.warn(`[publish] could not draw a formula as an image`, error)
    return null
  }
  finally {
    host.remove()
  }
}
