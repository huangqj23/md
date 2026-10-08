import { describe, expect, it } from 'vitest'
import { texToText } from './tex-text'

describe(`texToText`, () => {
  it.each([
    // Every inline formula of the Llama 3 notes.
    [String.raw`3.8 \times 10^{25}`, `3.8 × 10²⁵`],
    [String.raw`3 \times 10^{-4}`, `3 × 10⁻⁴`],
    [String.raw`\frac{2}{3} \cdot 4d`, `2/3 · 4d`],
    [String.raw`10^{22}`, `10²²`],
    [String.raw`2\text{–}4 \times 10^{-4}`, `2–4 × 10⁻⁴`],
    [String.raw`\frac{PP-1}{V \cdot M}`, `(PP − 1)/(V · M)`],
    [String.raw`O(S^2)`, `O(S²)`],
    [String.raw`\beta = 0.1`, `β = 0.1`],
    [String.raw`T_D`, `T_D`],
    [String.raw`\log 2`, `log 2`],
    [String.raw`6 \times 405\text{B} \times 15.6\text{T} = 3.79 \times 10^{25}`, `6 × 405B × 15.6T = 3.79 × 10²⁵`],
    [String.raw`2^{24}`, `2²⁴`],
    [String.raw`N^\star(3.8 \times 10^{25})`, `N*(3.8 × 10²⁵)`],
    [String.raw`C / (6N^\star)`, `C/(6N*)`],
    [String.raw`(\alpha, A) = (0.53, 0.29)`, `(α, A) = (0.53, 0.29)`],
    [String.raw`i`, `i`],
    [String.raw`2\pi \theta^{2i/128}`, `2πθ^(2i/128)`],
    [String.raw`N^\star(C) = AC^\alpha`, `N*(C) = AC^α`],
  ])(`%s → %s`, (tex, text) => {
    expect(texToText(tex)).toBe(text)
  })

  it(`handles signs, functions, delimiters and fonts`, () => {
    expect(texToText(`-x + y`)).toBe(`−x + y`)
    expect(texToText(String.raw`a = -\log(p)`)).toBe(`a = −log(p)`)
    expect(texToText(String.raw`\left( \frac{a+b}{2} \right)`)).toBe(`((a + b)/2)`)
    expect(texToText(String.raw`\mathcal{L}_{\mathrm{DPO}}`)).toBe(`ℒ_(DPO)`)
    expect(texToText(String.raw`x_1 \le \sqrt{n}`)).toBe(`x₁ ≤ √n`)
    expect(texToText(String.raw`\hat{y} \in \mathbb{R}`)).toBe(`ŷ ∈ ℝ`)
    expect(texToText(String.raw`\operatorname{softmax}(z)`)).toBe(`softmax(z)`)
  })

  it(`gives up on what it cannot write as text`, () => {
    expect(texToText(String.raw`\begin{pmatrix} a \end{pmatrix}`)).toBeNull()
    expect(texToText(String.raw`\sqrt[3]{x}`)).toBeNull()
    expect(texToText(String.raw`a \\ b`)).toBeNull()
    expect(texToText(`x^`)).toBeNull()
    expect(texToText(`{a`)).toBeNull()
    expect(texToText(`a}`)).toBeNull()
    expect(texToText(``)).toBeNull()
  })
})
