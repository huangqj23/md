import { describe, expect, it } from 'vitest'
import { findLocalImageFile } from './local-image-match'

/** A file as `<input webkitdirectory>` lists it, with its path inside the picked folder. */
function picked(relativePath: string): File {
  const file = new File([`x`], relativePath.split(`/`).pop()!)
  Object.defineProperty(file, `webkitRelativePath`, { value: relativePath })
  return file
}

describe(`findLocalImageFile`, () => {
  it(`prefers the file whose folders match the reference over a same-named copy deeper down`, () => {
    // A clean copy listed first must not win over the image the note links to.
    const files = [picked(`LLM_Notes/images/raw/react_fig1.png`), picked(`LLM_Notes/images/react_fig1.png`)]
    expect(findLocalImageFile(`images/react_fig1.png`, files)?.webkitRelativePath).toBe(`LLM_Notes/images/react_fig1.png`)
    expect(findLocalImageFile(`./images/react_fig1.png`, files)?.webkitRelativePath).toBe(`LLM_Notes/images/react_fig1.png`)
  })

  it(`takes the shallowest file when the reference names no folder`, () => {
    const files = [picked(`images/raw/a.png`), picked(`images/a.png`)]
    expect(findLocalImageFile(`a.png`, files)?.webkitRelativePath).toBe(`images/a.png`)
  })

  it(`still matches by file name alone, case-insensitively, and then without the extension`, () => {
    const files = [picked(`pics/Fig 2.PNG`), picked(`pics/chart.webp`)]
    expect(findLocalImageFile(`../elsewhere/fig%202.png`, files)?.name).toBe(`Fig 2.PNG`)
    expect(findLocalImageFile(`images/chart.png`, files)?.name).toBe(`chart.webp`)
    expect(findLocalImageFile(`images/missing.png`, files)).toBeUndefined()
  })
})
