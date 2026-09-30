# Third-party notices

The multi-platform publisher (`src/publish-agent/` and `src/services/publish/`)
was written for this repository, but the way it drives each platform's editor
— page URLs, DOM selectors, editor APIs, draft endpoints and login probes —
follows two Apache-2.0 projects. The code here is a rewrite, not a copy; each
file names the project it follows in its header comment.

| Project                                                                           | License    | What was followed                                                                                                                                                      |
| --------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [doocs/cose](https://github.com/doocs/cose)                                       | Apache-2.0 | WeChat, Zhihu (Markdown paste), CSDN (Markdown editor), Juejin, Baijiahao, Bilibili, Jianshu and Cnblogs editor flows; login probes in `services/publish/platforms.ts` |
| [leaperone/MultiPost-Extension](https://github.com/leaperone/MultiPost-Extension) | Apache-2.0 | Tab-injection approach, paste-based filling (Zhihu HTML fallback, Toutiao), Jianshu note creation, Cnblogs editor detection                                            |

Changes relative to those projects: rewritten in TypeScript as one bundled
agent with a versioned request/result protocol, every fill verified by
measuring what the editor holds afterwards, fallbacks between strategies,
and nothing published automatically (at most a draft is saved).

The full license text is in [LICENSE-APACHE-2.0](./LICENSE-APACHE-2.0).
