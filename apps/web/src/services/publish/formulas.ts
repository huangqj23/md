import type { AgentArticle } from '@/publish-agent/protocol'
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
  const img = doc.createElement(`img`)
  img.setAttribute(`src`, image.src)
  img.setAttribute(`alt`, raw)
  img.setAttribute(`width`, String(Math.round(image.width)))
  img.setAttribute(`height`, String(Math.round(image.height)))
  img.setAttribute(`style`, `width: ${image.width}px; height: ${image.height}px; max-width: 100%;`)
  paragraph.append(img)
  return paragraph
}

/** Formula ink on the platform's white page (the WeChat HTML leaves it as `currentColor`). */
const FORMULA_COLOR = `#333333`
/** Body text size the formulas are measured against, as in md's default style. */
const FORMULA_FONT_SIZE = `16px`
/** Drawn at this multiple of the CSS size so the picture stays sharp on high-DPI screens. */
const FORMULA_SCALE = 3

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error(`formula SVG did not load`))
    image.src = src
  })
}

/** Draws a MathJax SVG on a canvas at its laid-out size: a PNG data URL, or null if the browser could not. */
export async function renderFormulaImage(svg: SVGSVGElement): Promise<FormulaImage | null> {
  const host = document.createElement(`div`)
  host.style.cssText = `position:fixed;left:-100000px;top:0;font-size:${FORMULA_FONT_SIZE};line-height:normal;pointer-events:none;z-index:-1`
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

    const canvas = document.createElement(`canvas`)
    canvas.width = Math.ceil(width * FORMULA_SCALE)
    canvas.height = Math.ceil(height * FORMULA_SCALE)
    canvas.getContext(`2d`)!.drawImage(image, 0, 0, canvas.width, canvas.height)
    return {
      src: canvas.toDataURL(`image/png`),
      width: Math.round(width * 100) / 100,
      height: Math.round(height * 100) / 100,
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
