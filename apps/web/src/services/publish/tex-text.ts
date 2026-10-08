/**
 * Plain-text rendering of simple TeX (`3.8 \times 10^{25}` → `3.8 × 10²⁵`), for editors that can show
 * neither SVG formulas nor inline images. Digit superscripts and subscripts use Unicode; other
 * scripts are written `x^(…)` / `x_(…)`, which stays unambiguous even if styling is lost (an
 * HTML `<sup>` that an editor strips would turn 10²² into "1022"). Anything it does not know
 * returns null, and the caller keeps the TeX source instead.
 */

type Kind = `ord` | `bin` | `rel` | `open` | `close` | `punct` | `op` | `space`

interface Piece {
  text: string
  kind: Kind
}

class Unsupported extends Error {}

const GREEK: Record<string, string> = {
  alpha: `α`,
  beta: `β`,
  gamma: `γ`,
  delta: `δ`,
  epsilon: `ε`,
  varepsilon: `ε`,
  zeta: `ζ`,
  eta: `η`,
  theta: `θ`,
  vartheta: `ϑ`,
  iota: `ι`,
  kappa: `κ`,
  lambda: `λ`,
  mu: `μ`,
  nu: `ν`,
  xi: `ξ`,
  pi: `π`,
  rho: `ρ`,
  sigma: `σ`,
  tau: `τ`,
  upsilon: `υ`,
  phi: `ϕ`,
  varphi: `φ`,
  chi: `χ`,
  psi: `ψ`,
  omega: `ω`,
  Gamma: `Γ`,
  Delta: `Δ`,
  Theta: `Θ`,
  Lambda: `Λ`,
  Xi: `Ξ`,
  Pi: `Π`,
  Sigma: `Σ`,
  Upsilon: `Υ`,
  Phi: `Φ`,
  Psi: `Ψ`,
  Omega: `Ω`,
}

const SYMBOLS: Record<string, Piece> = {
  times: { text: `×`, kind: `bin` },
  cdot: { text: `·`, kind: `bin` },
  pm: { text: `±`, kind: `bin` },
  mp: { text: `∓`, kind: `bin` },
  div: { text: `÷`, kind: `bin` },
  ast: { text: `∗`, kind: `bin` },
  star: { text: `⋆`, kind: `bin` },
  circ: { text: `∘`, kind: `bin` },
  cup: { text: `∪`, kind: `bin` },
  cap: { text: `∩`, kind: `bin` },
  otimes: { text: `⊗`, kind: `bin` },
  oplus: { text: `⊕`, kind: `bin` },
  le: { text: `≤`, kind: `rel` },
  leq: { text: `≤`, kind: `rel` },
  ge: { text: `≥`, kind: `rel` },
  geq: { text: `≥`, kind: `rel` },
  ne: { text: `≠`, kind: `rel` },
  neq: { text: `≠`, kind: `rel` },
  ll: { text: `≪`, kind: `rel` },
  gg: { text: `≫`, kind: `rel` },
  approx: { text: `≈`, kind: `rel` },
  sim: { text: `∼`, kind: `rel` },
  simeq: { text: `≃`, kind: `rel` },
  equiv: { text: `≡`, kind: `rel` },
  propto: { text: `∝`, kind: `rel` },
  to: { text: `→`, kind: `rel` },
  rightarrow: { text: `→`, kind: `rel` },
  leftarrow: { text: `←`, kind: `rel` },
  gets: { text: `←`, kind: `rel` },
  Rightarrow: { text: `⇒`, kind: `rel` },
  Leftarrow: { text: `⇐`, kind: `rel` },
  leftrightarrow: { text: `↔`, kind: `rel` },
  mapsto: { text: `↦`, kind: `rel` },
  in: { text: `∈`, kind: `rel` },
  notin: { text: `∉`, kind: `rel` },
  subset: { text: `⊂`, kind: `rel` },
  subseteq: { text: `⊆`, kind: `rel` },
  supset: { text: `⊃`, kind: `rel` },
  mid: { text: `|`, kind: `rel` },
  infty: { text: `∞`, kind: `ord` },
  partial: { text: `∂`, kind: `ord` },
  nabla: { text: `∇`, kind: `ord` },
  forall: { text: `∀`, kind: `ord` },
  exists: { text: `∃`, kind: `ord` },
  emptyset: { text: `∅`, kind: `ord` },
  prime: { text: `′`, kind: `ord` },
  ldots: { text: `…`, kind: `ord` },
  cdots: { text: `⋯`, kind: `ord` },
  dots: { text: `…`, kind: `ord` },
  vert: { text: `|`, kind: `ord` },
  langle: { text: `⟨`, kind: `open` },
  rangle: { text: `⟩`, kind: `close` },
  lfloor: { text: `⌊`, kind: `open` },
  rfloor: { text: `⌋`, kind: `close` },
  lceil: { text: `⌈`, kind: `open` },
  rceil: { text: `⌉`, kind: `close` },
  sum: { text: `∑`, kind: `op` },
  prod: { text: `∏`, kind: `op` },
  int: { text: `∫`, kind: `op` },
}

const FUNCTIONS = new Set([`log`, `ln`, `exp`, `max`, `min`, `sup`, `inf`, `lim`, `arg`, `det`, `dim`, `sin`, `cos`, `tan`, `Pr`, `gcd`])
/** Font switches whose argument is still math: only the letters show. */
const MATH_FONTS = new Set([`mathrm`, `mathit`, `mathbf`, `mathsf`, `mathtt`, `boldsymbol`, `bm`])
const TEXT_MODE = new Set([`text`, `textrm`, `textit`, `textbf`, `textsf`, `texttt`, `mbox`])
const SIZED_DELIMITERS = new Set([`left`, `right`, `middle`, `big`, `Big`, `bigg`, `Bigg`, `bigl`, `bigr`, `Bigl`, `Bigr`, `biggl`, `biggr`, `Biggl`, `Biggr`])
const IGNORED = new Set([`displaystyle`, `textstyle`, `scriptstyle`, `limits`, `nolimits`])
const CALLIGRAPHIC: Record<string, string> = { B: `ℬ`, E: `ℰ`, F: `ℱ`, H: `ℋ`, I: `ℐ`, L: `ℒ`, M: `ℳ`, R: `ℛ` }
const BLACKBOARD: Record<string, string> = { C: `ℂ`, N: `ℕ`, Q: `ℚ`, R: `ℝ`, Z: `ℤ`, E: `𝔼`, P: `ℙ` }
const ACCENTS: Record<string, string> = { hat: `̂`, widehat: `̂`, bar: `̄`, overline: `̄`, tilde: `̃`, widetilde: `̃`, dot: `̇`, ddot: `̈`, vec: `⃗` }

const SUPERSCRIPTS: Record<string, string> = {
  '0': `⁰`,
  '1': `¹`,
  '2': `²`,
  '3': `³`,
  '4': `⁴`,
  '5': `⁵`,
  '6': `⁶`,
  '7': `⁷`,
  '8': `⁸`,
  '9': `⁹`,
  '+': `⁺`,
  '-': `⁻`,
  '−': `⁻`,
  '=': `⁼`,
  '(': `⁽`,
  ')': `⁾`,
  'i': `ⁱ`,
  'n': `ⁿ`,
}
const SUBSCRIPTS: Record<string, string> = {
  '0': `₀`,
  '1': `₁`,
  '2': `₂`,
  '3': `₃`,
  '4': `₄`,
  '5': `₅`,
  '6': `₆`,
  '7': `₇`,
  '8': `₈`,
  '9': `₉`,
  '+': `₊`,
  '-': `₋`,
  '−': `₋`,
  '=': `₌`,
  '(': `₍`,
  ')': `₎`,
}

function script(content: string, marker: `^` | `_`): string {
  const chars = Array.from(content)
  if (marker === `^` && [`*`, `⋆`, `∗`].includes(content))
    return `*`
  if (marker === `^` && chars.every(char => char === `′`))
    return content
  const map = marker === `^` ? SUPERSCRIPTS : SUBSCRIPTS
  if (chars.length && chars.every(char => map[char]))
    return chars.map(char => map[char]).join(``)
  return chars.length === 1 ? `${marker}${content}` : `${marker}(${content})`
}

/** A fraction's numerator or denominator, in parentheses unless it is a single term. */
function operand(text: string): string {
  return /^[\p{L}\p{N}.′*⋆]+$/u.test(text) ? text : `(${text})`
}

function charPiece(char: string): Piece {
  if (char === `-`)
    return { text: `−`, kind: `bin` }
  if (char === `+`)
    return { text: `+`, kind: `bin` }
  if (`=<>:`.includes(char))
    return { text: char, kind: `rel` }
  if (`([`.includes(char))
    return { text: char, kind: `open` }
  if (`)]`.includes(char))
    return { text: char, kind: `close` }
  if (`,;`.includes(char))
    return { text: char, kind: `punct` }
  if (char === `'`)
    return { text: `′`, kind: `ord` }
  if (char === `~`)
    return { text: ` `, kind: `space` }
  if (`&#$%\\`.includes(char))
    throw new Unsupported(char)
  return { text: char, kind: `ord` }
}

function join(pieces: readonly Piece[]): string {
  let out = ``
  let prev: Kind | null = null
  for (const piece of pieces) {
    const kind = piece.kind
    if (kind === `space`) {
      out += ` `
      continue
    }
    // A minus or plus with nothing to its left is a sign, not an operation; it binds to what follows.
    if (kind === `bin` && (prev === null || prev === `bin` || prev === `rel` || prev === `open` || prev === `punct` || prev === `op`)) {
      out += piece.text
      prev = `open`
      continue
    }
    if (kind === `bin` || kind === `rel`)
      out = `${out.trimEnd()} ${piece.text} `
    else if (kind === `punct`)
      out = `${out.trimEnd()}${piece.text} `
    else if (kind === `op`)
      out += `${prev === `ord` || prev === `close` ? ` ` : ``}${piece.text} `
    else if (kind === `open` && prev === `op`)
      out = `${out.trimEnd()}${piece.text}`
    else if (kind === `close`)
      out = `${out.trimEnd()}${piece.text}`
    else
      out += piece.text
    prev = kind
  }
  return out.replace(/ {2,}/g, ` `).trim()
}

class Parser {
  pos = 0
  constructor(private readonly src: string) {}

  get done(): boolean {
    return this.pos >= this.src.length
  }

  skipSpaces() {
    while (!this.done && /\s/.test(this.src[this.pos]))
      this.pos++
  }

  /** Atoms up to the end or the closing brace of the current group. */
  sequence(): Piece[] {
    const out: Piece[] = []
    for (this.skipSpaces(); !this.done && this.src[this.pos] !== `}`; this.skipSpaces()) {
      const char = this.src[this.pos]
      if (char === `^` || char === `_`) {
        this.pos++
        const content = join(this.atom())
        const base = out.pop()
        out.push({ text: `${base?.text ?? ``}${script(content, char)}`, kind: base && base.kind !== `space` ? base.kind : `ord` })
        continue
      }
      out.push(...this.atom())
    }
    return out
  }

  /** One group, command with its arguments, or character. */
  atom(): Piece[] {
    this.skipSpaces()
    if (this.done)
      throw new Unsupported(`missing argument`)
    const char = this.src[this.pos]
    if (char === `{`) {
      this.pos++
      const inner = this.sequence()
      if (this.src[this.pos] !== `}`)
        throw new Unsupported(`unclosed group`)
      this.pos++
      return [{ text: join(inner), kind: `ord` }]
    }
    if (char === `}`)
      throw new Unsupported(`unexpected }`)
    if (char === `\\`)
      return this.command()
    this.pos++
    return [charPiece(char)]
  }

  argumentText(): string {
    return join(this.atom())
  }

  /** A text-mode argument, kept as written. */
  rawGroup(): string {
    this.skipSpaces()
    if (this.src[this.pos] !== `{`)
      return this.argumentText()
    const end = this.src.indexOf(`}`, this.pos)
    const content = end === -1 ? `` : this.src.slice(this.pos + 1, end)
    if (end === -1 || /[\\{$]/.test(content))
      throw new Unsupported(`text argument`)
    this.pos = end + 1
    return content
  }

  command(): Piece[] {
    this.pos++
    let name = this.src[this.pos] ?? ``
    if (/[a-z]/i.test(name)) {
      const match = /^[a-z]+/i.exec(this.src.slice(this.pos))!
      name = match[0]
    }
    this.pos += name.length
    if (!name)
      throw new Unsupported(`trailing backslash`)

    if (GREEK[name])
      return [{ text: GREEK[name], kind: `ord` }]
    if (SYMBOLS[name])
      return [SYMBOLS[name]]
    if (FUNCTIONS.has(name))
      return [{ text: name, kind: `op` }]
    if (IGNORED.has(name))
      return []
    switch (name) {
      case `{`: return [{ text: `{`, kind: `open` }]
      case `}`: return [{ text: `}`, kind: `close` }]
      case `|`: return [{ text: `‖`, kind: `ord` }]
      case `%`: case `_`: case `#`: case `&`: case `$`: return [{ text: name, kind: `ord` }]
      case `,`: case `:`: case `;`: case `>`: case ` `: case `quad`: case `qquad`: return [{ text: ` `, kind: `space` }]
      case `!`: return []
      case `operatorname`: return [{ text: this.rawGroup(), kind: `op` }]
      case `frac`: case `dfrac`: case `tfrac`: {
        const numerator = this.argumentText()
        const denominator = this.argumentText()
        return [{ text: `${operand(numerator)}/${operand(denominator)}`, kind: `ord` }]
      }
      case `sqrt`: {
        this.skipSpaces()
        if (this.src[this.pos] === `[`)
          throw new Unsupported(`root index`)
        return [{ text: `√${operand(this.argumentText())}`, kind: `ord` }]
      }
      case `mathcal`: case `mathscr`:
        return [{ text: Array.from(this.argumentText(), char => CALLIGRAPHIC[char] ?? char).join(``), kind: `ord` }]
      case `mathbb`: {
        const letters = Array.from(this.argumentText())
        if (!letters.every(char => BLACKBOARD[char]))
          throw new Unsupported(`mathbb`)
        return [{ text: letters.map(char => BLACKBOARD[char]).join(``), kind: `ord` }]
      }
    }
    if (MATH_FONTS.has(name))
      return [{ text: this.argumentText(), kind: `ord` }]
    if (TEXT_MODE.has(name))
      return [{ text: this.rawGroup(), kind: `ord` }]
    if (SIZED_DELIMITERS.has(name)) {
      this.skipSpaces()
      if (this.src[this.pos] === `.`) {
        this.pos++
        return []
      }
      return this.atom()
    }
    if (ACCENTS[name]) {
      const base = Array.from(this.argumentText())
      if (base.length !== 1)
        throw new Unsupported(`accent over several characters`)
      return [{ text: base[0] + ACCENTS[name], kind: `ord` }]
    }
    throw new Unsupported(name)
  }
}

export function texToText(tex: string): string | null {
  const parser = new Parser(tex)
  try {
    const pieces = parser.sequence()
    if (!parser.done)
      return null
    // Precomposed where Unicode has it (ŷ), so accents do not depend on the font placing them.
    const text = join(pieces).normalize(`NFC`)
    return text || null
  }
  catch (error) {
    if (error instanceof Unsupported)
      return null
    throw error
  }
}
