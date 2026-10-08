// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { buildAgentArticle } from './article'
import { splitArticle } from './split'

function section(heading: string, size: number) {
  return [
    `## ${heading}`,
    `正文${`字`.repeat(size)}，带一个公式 $E=mc^2$。`,
    `| 模型 | 参数 |`,
    `| --- | --- |`,
    `| Llama 3 | 405B |`,
    `\`\`\`python`,
    `# 注释不是标题`,
    `print("hi")`,
    `\`\`\``,
  ].join(`\n`)
}

describe(`splitArticle with the real renderer`, () => {
  it(`splits a rendered article into parts that each fit the limit`, async () => {
    const markdown = [`# 论文笔记`, `引言`, section(`一`, 900), section(`二`, 900), section(`三`, 900), section(`四`, 900)].join(`\n\n`)
    const article = await buildAgentArticle({ title: `论文笔记`, summary: ``, markdown, wechatHtml: `` })
    const limit = Math.ceil(article.textLength * 0.7)

    const parts = await splitArticle(article, {
      limit,
      partTitle: (title, index) => `${title}（${[`上`, `下`][index]}）`,
      continuedIn: title => `（未完，接下篇《${title}》）`,
      continuedFrom: title => `（接上篇《${title}》）`,
    })

    expect(parts).toHaveLength(2)
    expect(parts!.every(part => part.textLength > 0 && part.textLength <= limit)).toBe(true)
    expect(parts![0].html).toContain(`<h2>一</h2>`)
    expect(parts![1].html).toContain(`<h2>三</h2>`)
    expect(parts![1].html).toContain(`接上篇`)
    // Code blocks stay whole: the comment line inside one never starts a part.
    expect(parts!.every(part => (part.markdown.match(/```/g) ?? []).length % 2 === 0)).toBe(true)
  })
})
