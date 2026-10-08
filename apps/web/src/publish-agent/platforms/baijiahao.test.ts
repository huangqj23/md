// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { dropListMarkers } from './baijiahao'

describe(`dropListMarkers`, () => {
  it(`removes the marker md writes into each item, keeping the item's formatting`, () => {
    const html = `<ol style="list-style: none"><li style="margin: 0">4. <strong style="color: #c00">评测口径要小心</strong>：正文</li></ol>`
      + `<ul><li>• <strong>Llama 3.2</strong>（2024-09）</li></ul>`
    expect(dropListMarkers(html)).toBe(
      `<ol style="list-style: none"><li style="margin: 0"><strong style="color: #c00">评测口径要小心</strong>：正文</li></ol>`
      + `<ul><li><strong>Llama 3.2</strong>（2024-09）</li></ul>`,
    )
  })

  it(`handles markers in nested lists and leaves other text alone`, () => {
    const html = `<ul><li>• 外层<ul><li>• 内层</li></ul></li></ul><p>3. 不是列表</p><ol><li>1. 2024. 以年份开头的条目</li></ol>`
    expect(dropListMarkers(html)).toBe(`<ul><li>外层<ul><li>内层</li></ul></li></ul><p>3. 不是列表</p><ol><li>2024. 以年份开头的条目</li></ol>`)
  })
})
