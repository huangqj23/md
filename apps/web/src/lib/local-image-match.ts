function decodePath(path: string): string {
  try {
    return decodeURIComponent(path)
  }
  catch {
    return path
  }
}

/** Folder names along a path, lowercased, without `.`/`..` and without the file name. */
function folders(path: string): string[] {
  return path.split(/[/\\]/).filter(part => part && part !== `.` && part !== `..`).slice(0, -1).map(part => part.toLowerCase())
}

function fileName(path: string): string {
  const parts = path.split(/[/\\]/)
  return (parts[parts.length - 1] ?? ``).toLowerCase()
}

/** How many folders, counted from the file upwards, two paths share. */
function sharedFolders(a: string[], b: string[]): number {
  let shared = 0
  while (shared < a.length && shared < b.length && a[a.length - 1 - shared] === b[b.length - 1 - shared])
    shared++
  return shared
}

const withoutExtension = (name: string) => name.replace(/\.[^.]+$/, ``)

/**
 * The file a local image path in Markdown refers to, among the files of a folder the user picked
 * (an `<input webkitdirectory>` list, so every subfolder is in it). Matching is by file name, then by
 * name without extension. When several files share the name, e.g. `images/fig.png` and a clean copy
 * in `images/raw/fig.png`, the one whose folders end like the reference wins, then the shallowest.
 */
export function findLocalImageFile(path: string, files: readonly File[]): File | undefined {
  const reference = decodePath(path)
  const name = fileName(reference)
  if (!name)
    return undefined
  const wanted = folders(reference)
  const best = (candidates: File[]) => {
    let pick: File | undefined
    let pickShared = -1
    let pickDepth = Number.POSITIVE_INFINITY
    for (const file of candidates) {
      const location = folders(file.webkitRelativePath || file.name)
      const shared = sharedFolders(wanted, location)
      if (shared > pickShared || (shared === pickShared && location.length < pickDepth)) {
        pick = file
        pickShared = shared
        pickDepth = location.length
      }
    }
    return pick
  }
  const exact = files.filter(file => file.name.toLowerCase() === name)
  if (exact.length)
    return best(exact)
  const base = withoutExtension(name)
  return best(files.filter(file => withoutExtension(file.name.toLowerCase()) === base))
}
