import type { SplitDeps, SplitOptions } from './split'
import { describe, expect, it } from 'vitest'
import { chooseCuts, definitionsUsedIn, outlineArticle, splitArticle } from './split'

const count = (markdown: string) => markdown.replace(/\s+/g, ``).length

/** Measures Markdown by its non-whitespace characters, without rendering. */
const deps: SplitDeps = {
  measure: async markdown => count(markdown),
  build: async input => ({ ...input, html: ``, wechatHtml: ``, textLength: count(input.markdown) }),
}

const options: SplitOptions = {
  limit: 3000,
  partTitle: (title, index, total) => `${title}（${total === 2 ? [`上`, `下`][index] : `${index + 1}/${total}`}）`,
  continuedIn: title => `（未完，接下篇《${title}》）`,
  continuedFrom: title => `（接上篇《${title}》）`,
}

const filler = (size: number) => `字`.repeat(size)

describe(`outlineArticle`, () => {
  it(`cuts before headings, but not inside code blocks or front matter`, () => {
    const { sections } = outlineArticle([
      `---`,
      `# not a heading: front matter`,
      `---`,
      `intro`,
      `## One`,
      `\`\`\`python`,
      `# a comment, not a heading`,
      `\`\`\``,
      `### One point one`,
      `text`,
    ].join(`\n`))
    expect(sections.map(section => section.level)).toEqual([0, 2, 3])
    expect(sections[0].markdown).toContain(`front matter`)
    expect(sections[1].markdown).toContain(`# a comment, not a heading`)
  })

  it(`takes footnote and link definitions out of the sections`, () => {
    const { sections, definitions } = outlineArticle(`## A\nsee[^1] and [paper][p]\n\n[^1]: a note\n[p]: https://arxiv.org/abs/2407.21783`)
    expect(sections[0].markdown).not.toContain(`[^1]:`)
    expect(definitions).toEqual([
      { id: `1`, footnote: true, line: `[^1]: a note` },
      { id: `p`, footnote: false, line: `[p]: https://arxiv.org/abs/2407.21783` },
    ])
    expect(definitionsUsedIn(`only[^1]`, definitions).map(item => item.id)).toEqual([`1`])
  })
})

describe(`chooseCuts`, () => {
  it(`finds the most even split that keeps every part under the cap`, () => {
    expect(chooseCuts([10, 50, 40, 60, 40], [0, 2, 3, 2, 3], 2, 120, 2)).toEqual([0, 3])
  })

  it(`only cuts at headings of the allowed level`, () => {
    // An h3 cut would give 100 / 100, but h2 cuts can only give 10 / 190.
    expect(chooseCuts([10, 90, 100], [0, 2, 3], 2, 150, 2)).toBeNull()
    expect(chooseCuts([10, 90, 100], [0, 2, 3], 2, 150, 3)).toEqual([0, 2])
  })

  it(`gives up when a single section is over the cap`, () => {
    expect(chooseCuts([10, 500, 10], [0, 2, 2], 2, 200, 2)).toBeNull()
  })
})

describe(`splitArticle`, () => {
  const article = {
    title: `论文学习笔记：Llama 3`,
    summary: `摘要`,
    markdown: [
      `开头${filler(200)}[^1]`,
      `## 背景\n${filler(900)}`,
      `## 方法\n${filler(900)}`,
      `### 细节\n${filler(400)}`,
      `## 实验\n${filler(900)}`,
      `## 结论\n${filler(500)}[^2]`,
      `[^1]: 第一条注释`,
      `[^2]: 第二条注释`,
    ].join(`\n\n`),
  }

  it(`splits an over-long article into 上/下 at a level-2 heading, even when an h3 cut is more even`, async () => {
    const parts = await splitArticle(article, options, deps)

    expect(parts?.map(part => part.title)).toEqual([`论文学习笔记：Llama 3（上）`, `论文学习笔记：Llama 3（下）`])
    const [first, second] = parts!
    expect(first.markdown).toContain(`（未完，接下篇《论文学习笔记：Llama 3（下）》）`)
    expect(second.markdown.startsWith(`（接上篇《论文学习笔记：Llama 3（上）》）\n\n## `)).toBe(true)
    expect(first.markdown).toContain(`[^1]: 第一条注释`)
    expect(first.markdown).not.toContain(`[^2]:`)
    expect(second.markdown).toContain(`[^2]: 第二条注释`)
    expect(parts!.every(part => part.textLength <= options.limit)).toBe(true)
    expect(parts!.every(part => part.summary === `摘要`)).toBe(true)
  })

  it(`keeps part titles within the platform's title limit`, async () => {
    const parts = await splitArticle({ ...article, title: `很长的标题`.repeat(10) }, { ...options, titleMaxLength: 20 }, deps)
    expect(parts?.every(part => [...part.title].length <= 20 && part.title.endsWith(`）`))).toBe(true)
  })

  it(`uses more parts when two would still be over the limit`, async () => {
    const parts = await splitArticle(article, { ...options, limit: 1700 }, deps)
    expect(parts).toHaveLength(3)
    expect(parts!.map(part => part.title.slice(-5))).toEqual([`（1/3）`, `（2/3）`, `（3/3）`])
  })

  it(`gives up on an article without headings`, async () => {
    expect(await splitArticle({ ...article, markdown: filler(5000) }, options, deps)).toBeNull()
  })
})
