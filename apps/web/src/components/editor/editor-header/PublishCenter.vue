<script setup lang="ts">
import type { PublishPlatformId } from '@/publish-agent/protocol'
import type { CenterFilter, PlatformState, PlatformView } from '@/services/publish/center'
import { TriangleAlert } from '@lucide/vue'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { buildPlatformViews, CENTER_FILTERS, errorMessageKey, formatNameList, formatPublishTime, matchesFilter, outdatedViews } from '@/services/publish/center'
import { PUBLISH_PLATFORMS } from '@/services/publish/platforms'
import { contentHash, versionNumber } from '@/services/publish/records'
import { useEditorStore } from '@/stores/editor'
import { usePostStore } from '@/stores/post'
import { usePublishStore } from '@/stores/publish'
import { useUIStore } from '@/stores/ui'
import PublishCenterDetail from './PublishCenterDetail.vue'
import PublishStatePill from './PublishStatePill.vue'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t, locale } = useI18n()
const publishStore = usePublishStore()
const postStore = usePostStore()
const uiStore = useUIStore()
const { records, running, cancelRequested } = storeToRefs(publishStore)
const { posts, currentPostId } = storeToRefs(postStore)

const dialogOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit(`update:open`, value),
})

// The version check hashes the post content, which trails the editor by a debounce.
useEditorStore().flushContentToPostStore()

const viewedPostId = ref(uiStore.publishCenterPostId ?? currentPostId.value)
const viewedPost = computed(() => postStore.getPostById(viewedPostId.value) ?? postStore.currentPost)
const record = computed(() => viewedPost.value ? records.value[viewedPost.value.id] : undefined)
const isCurrentPost = computed(() => viewedPost.value?.id === currentPostId.value)
const currentVersion = computed(() => contentHash(viewedPost.value?.content ?? ``))
const currentVersionNumber = computed(() => versionNumber(record.value, currentVersion.value))
const canSync = computed(() => isCurrentPost.value && !running.value)

/** Posts with a publish record, latest first, plus the post open in the editor. */
const postOptions = computed(() => {
  const order = (id: string) => id === currentPostId.value ? Number.POSITIVE_INFINITY : records.value[id]?.updatedAt ?? 0
  return posts.value
    .filter(post => post.id === currentPostId.value || records.value[post.id])
    .sort((a, b) => order(b.id) - order(a.id))
})

const views = computed(() => buildPlatformViews(
  PUBLISH_PLATFORMS,
  record.value,
  currentVersion.value,
  viewedPost.value ? publishStore.liveRunsFor(viewedPost.value.id) : [],
))

const filter = ref<CenterFilter>(`all`)
const selectedId = ref<PublishPlatformId | null>(null)

watch(viewedPostId, () => {
  filter.value = `all`
  selectedId.value = null
})

const filterCounts = computed(() => Object.fromEntries(
  CENTER_FILTERS.map(name => [name, views.value.filter(view => matchesFilter(view, name)).length]),
) as Record<CenterFilter, number>)
const visibleViews = computed(() => views.value.filter(view => matchesFilter(view, filter.value)))
const selectedView = computed(() =>
  views.value.find(view => view.platform.id === selectedId.value)
  ?? views.value.find(view => view.state !== `idle`)
  ?? views.value[0],
)

function time(at: number): string {
  return formatPublishTime(at, locale.value)
}

function platformName(id: PublishPlatformId): string {
  return t(`publish.platforms.${id}`)
}

const articleTitle = computed(() => record.value?.title || viewedPost.value?.title || ``)

const versionLine = computed(() => {
  if (!record.value)
    return ``
  const parts = [
    currentVersionNumber.value === null
      ? t(`publish.center.versionUnsynced`)
      : t(`publish.center.versionCurrent`, { version: currentVersionNumber.value }),
    t(`publish.center.lastSync`, { time: time(record.value.updatedAt) }),
  ]
  return parts.join(` · `)
})

/** Order and colour of the progress bar; platforms never synced are left out. */
const SEGMENTS: { states: PlatformState[], tone: string }[] = [
  { states: [`published`], tone: `bg-green-600` },
  { states: [`draft`], tone: `bg-blue-500` },
  { states: [`attention`], tone: `bg-amber-500` },
  { states: [`failed`, `login-required`], tone: `bg-red-600` },
  { states: [`queued`, `syncing`], tone: `bg-muted-foreground/40` },
]

const progress = computed(() => {
  const count = (states: PlatformState[]) => views.value.filter(view => states.includes(view.state)).length
  const segments: string[] = []
  for (const segment of SEGMENTS) {
    for (let i = count(segment.states); i > 0; i--)
      segments.push(segment.tone)
  }
  return {
    tracked: views.value.filter(view => view.state !== `idle`).length,
    published: count([`published`]),
    pending: count([`draft`, `attention`]),
    failed: count([`failed`, `login-required`]),
    syncing: count([`queued`, `syncing`]),
    segments,
  }
})

const progressLabel = computed(() => t(`publish.center.progress`, { published: progress.value.published, total: progress.value.tracked }))
const progressDetail = computed(() => {
  const parts = [t(`publish.center.progressDetail`, { pending: progress.value.pending, failed: progress.value.failed })]
  if (progress.value.syncing)
    parts.push(t(`publish.center.progressSyncing`, { count: progress.value.syncing }))
  return parts.join(` · `)
})

const outdated = computed(() => outdatedViews(views.value))
const outdatedSentences = computed(() => {
  const names = (list: PlatformView[]) => formatNameList(list.map(view => platformName(view.platform.id)), locale.value)
  const sentences = [t(`publish.center.outdated.intro`)]
  if (outdated.value.drafts.length)
    sentences.push(t(`publish.center.outdated.drafts`, { names: names(outdated.value.drafts) }))
  if (outdated.value.published.length)
    sentences.push(t(`publish.center.outdated.published`, { names: names(outdated.value.published) }))
  return sentences
})

function brief(view: PlatformView): string {
  const record = view.record
  switch (view.state) {
    case `queued`:
    case `syncing`:
      return t(`publish.center.brief.syncing`)
    case `published`:
      return t(`publish.center.brief.published`, { version: view.heldVersion ?? `?`, time: record?.published ? time(record.published.at) : `` })
    case `draft`:
      return t(`publish.center.brief.ready`, { version: view.heldVersion ?? `?` })
    case `attention`: {
      const issues = view.checks.filter(check => check.level === `warn`).map(check => t(`publish.center.issues.${check.key}`))
      return [t(`publish.center.brief.version`, { version: view.heldVersion ?? `?` }), ...issues].join(` · `)
    }
    case `failed`:
    case `login-required`:
      return t(errorMessageKey(record?.errorCode))
    default:
      return t(`publish.center.brief.idle`)
  }
}

async function syncPlatforms(ids: PublishPlatformId[]) {
  if (!ids.length || !canSync.value)
    return
  // First await in the click handler: the permission prompt needs the user gesture.
  if (!(await publishStore.requestPermissions())) {
    toast.error(t(`publish.permission.denied`))
    return
  }
  await publishStore.resync(ids)
}

async function openTab(id: PublishPlatformId) {
  if (!viewedPost.value)
    return
  if (!(await publishStore.openPlatformTab(viewedPost.value.id, id)))
    toast.info(t(`publish.center.tabClosed`))
}

function openInEditor() {
  if (viewedPost.value)
    currentPostId.value = viewedPost.value.id
}
</script>

<template>
  <Dialog v-model:open="dialogOpen">
    <DialogContent class="!max-w-none w-[min(1280px,96vw)] h-[min(920px,94vh)] flex flex-col gap-0 overflow-hidden p-0">
      <header class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-6 py-3 pe-14">
        <DialogTitle class="text-base font-semibold">
          {{ t('publish.center.title') }}
        </DialogTitle>
        <DialogDescription class="sr-only">
          {{ t('publish.center.description') }}
        </DialogDescription>
        <div class="flex-1" />
        <Button v-if="running" variant="outline" size="sm" :disabled="cancelRequested" @click="publishStore.cancel()">
          {{ cancelRequested ? t('publish.stopping') : t('publish.stop') }}
        </Button>
        <Button size="sm" :disabled="running" @click="uiStore.openPublishDialog()">
          {{ t('publish.center.syncDialog') }}
        </Button>
      </header>

      <div class="flex-1 overflow-y-auto bg-muted/30">
        <div class="flex flex-col gap-4 p-4 sm:p-6">
          <section aria-labelledby="publish-center-article" class="flex flex-wrap items-end gap-x-10 gap-y-4 rounded-lg border bg-background p-5">
            <div class="min-w-0 flex-[999_1_420px]">
              <div v-if="postOptions.length > 1" class="flex flex-wrap items-center gap-2">
                <span id="publish-center-post-label" class="text-xs text-muted-foreground">{{ t('publish.center.articleLabel') }}</span>
                <Select v-model="viewedPostId">
                  <SelectTrigger class="h-8 w-auto max-w-full gap-2 text-xs" aria-labelledby="publish-center-post-label">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="post in postOptions" :key="post.id" :value="post.id">
                      {{ post.title }}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <h2 id="publish-center-article" class="mt-2 break-words text-lg font-semibold leading-snug">
                {{ articleTitle }}
              </h2>
              <p v-if="versionLine" class="mt-1 text-sm text-muted-foreground">
                {{ versionLine }}
              </p>
            </div>
            <div v-if="progress.tracked" class="flex min-w-0 flex-[1_1_300px] flex-col gap-2">
              <div class="flex flex-wrap justify-between gap-x-3 text-sm">
                <span class="font-medium">{{ progressLabel }}</span>
                <span class="text-muted-foreground">{{ progressDetail }}</span>
              </div>
              <div role="img" :aria-label="`${progressLabel}, ${progressDetail}`" class="flex gap-[3px]">
                <span v-for="(tone, index) in progress.segments" :key="index" class="h-2 flex-1 rounded-sm" :class="tone" />
              </div>
            </div>
          </section>

          <Alert v-if="!isCurrentPost">
            <AlertDescription class="flex flex-wrap items-center justify-between gap-2">
              <span>{{ t('publish.center.notCurrent') }}</span>
              <Button size="sm" variant="outline" @click="openInEditor">
                {{ t('publish.center.openInEditor') }}
              </Button>
            </AlertDescription>
          </Alert>

          <p v-if="!record" class="rounded-lg border border-dashed bg-background p-6 text-center text-sm text-muted-foreground">
            {{ t('publish.center.never') }}
          </p>

          <div
            v-if="outdated.drafts.length || outdated.published.length"
            role="status"
            class="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-950 dark:border-orange-900 dark:bg-orange-950/40 dark:text-orange-100"
          >
            <TriangleAlert class="size-4 shrink-0" aria-hidden="true" />
            <p class="min-w-0 flex-[999_1_360px]">
              <span v-for="(sentence, index) in outdatedSentences" :key="index" class="me-1">{{ sentence }}</span>
              <span v-if="outdated.drafts.length" class="mt-0.5 block text-xs opacity-80">{{ t('publish.center.outdated.note') }}</span>
            </p>
            <Button
              v-if="outdated.drafts.length"
              variant="outline"
              size="sm"
              :disabled="!canSync"
              @click="syncPlatforms(outdated.drafts.map(view => view.platform.id))"
            >
              {{ t('publish.center.outdated.resync', { count: outdated.drafts.length }) }}
            </Button>
          </div>

          <div class="flex flex-wrap items-start gap-4">
            <section class="flex min-w-0 flex-[999_1_540px] flex-col gap-3" :aria-label="t('publish.center.platformsLabel')">
              <div role="group" :aria-label="t('publish.center.filtersLabel')" class="flex flex-wrap gap-1.5">
                <button
                  v-for="name in CENTER_FILTERS"
                  :key="name"
                  type="button"
                  :aria-pressed="filter === name"
                  class="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-sm transition-colors focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
                  :class="filter === name ? 'border-primary bg-primary text-primary-foreground' : 'bg-background hover:bg-accent'"
                  @click="filter = name"
                >
                  {{ t(`publish.center.filters.${name}`) }}
                  <span class="text-xs tabular-nums opacity-80">{{ filterCounts[name] }}</span>
                </button>
              </div>

              <ul v-if="visibleViews.length" class="divide-y overflow-hidden rounded-lg border bg-background">
                <li
                  v-for="view in visibleViews"
                  :key="view.platform.id"
                  class="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2"
                  :class="selectedView?.platform.id === view.platform.id ? 'bg-accent/60' : ''"
                >
                  <button
                    type="button"
                    class="flex min-h-11 min-w-0 flex-[999_1_360px] flex-wrap items-center gap-x-3 gap-y-1 rounded-sm text-start focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
                    :aria-pressed="selectedView?.platform.id === view.platform.id"
                    @click="selectedId = view.platform.id"
                  >
                    <span class="w-24 shrink-0 text-sm font-medium">{{ platformName(view.platform.id) }}</span>
                    <PublishStatePill :view="view" />
                    <span class="min-w-0 flex-[1_1_180px] text-sm text-muted-foreground">{{ brief(view) }}</span>
                  </button>
                  <div class="ms-auto flex items-center gap-1.5">
                    <span v-if="view.record" class="text-xs tabular-nums text-muted-foreground">{{ time(view.record.at) }}</span>
                    <Button
                      v-if="view.state === 'draft' || view.state === 'attention'"
                      size="xs"
                      variant="ghost"
                      @click="openTab(view.platform.id)"
                    >
                      {{ t('publish.center.actions.openDraft') }}
                    </Button>
                    <Button
                      v-if="view.state === 'failed' || view.state === 'login-required'"
                      size="xs"
                      variant="outline"
                      :disabled="!canSync"
                      @click="syncPlatforms([view.platform.id])"
                    >
                      {{ t('publish.center.actions.retry') }}
                    </Button>
                  </div>
                </li>
              </ul>
              <p v-else class="rounded-lg border border-dashed bg-background p-6 text-center text-sm text-muted-foreground">
                {{ t('publish.center.emptyFilter') }}
              </p>
            </section>

            <PublishCenterDetail
              v-if="selectedView && viewedPost"
              class="min-w-0 flex-[1_1_360px]"
              :view="selectedView"
              :record="record"
              :post-id="viewedPost.id"
              :can-sync="canSync"
              @sync="id => syncPlatforms([id])"
              @open-tab="openTab"
            />
          </div>
        </div>
      </div>
    </DialogContent>
  </Dialog>
</template>
