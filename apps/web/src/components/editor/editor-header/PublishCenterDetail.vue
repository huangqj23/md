<script setup lang="ts">
import type { PublishPlatformId } from '@/publish-agent/protocol'
import type { Check, CheckLevel, PlatformView } from '@/services/publish/center'
import type { PublishEvent, PublishRecord } from '@/services/publish/records'
import { Check as CheckIcon, CircleX, TriangleAlert } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { errorMessageKey, formatPublishTime } from '@/services/publish/center'
import { normalizePublishedUrl, versionNumber } from '@/services/publish/records'
import { usePublishStore } from '@/stores/publish'
import PublishStatePill from './PublishStatePill.vue'

const props = defineProps<{
  view: PlatformView
  record?: PublishRecord
  postId: string
  /** False while another sync runs or when this is not the post in the editor. */
  canSync: boolean
}>()

const emit = defineEmits<{
  sync: [id: PublishPlatformId]
  openTab: [id: PublishPlatformId]
}>()

const { t, locale } = useI18n()
const publishStore = usePublishStore()

const url = ref(``)
const urlError = ref(``)

watch(() => props.view.platform.id, () => {
  url.value = ``
  urlError.value = ``
})

const id = computed(() => props.view.platform.id)
const platformRecord = computed(() => props.view.record)

function time(at: number): string {
  return formatPublishTime(at, locale.value)
}

function versionOf(version: string | undefined): number | string {
  return versionNumber(props.record, version) ?? `?`
}

/** Rendered as separate spans so each language keeps its own sentence spacing. */
const sentences = computed((): string[] => {
  const { state, outdated } = props.view
  const record = platformRecord.value
  const previousDraft = record?.draft ? [t(`publish.center.sentence.previousDraft`, { version: versionOf(record.draft.version) })] : []
  switch (state) {
    case `queued`:
      return [t(`publish.center.sentence.queued`)]
    case `syncing`:
      return [t(`publish.center.sentence.syncing`)]
    case `draft`:
    case `attention`: {
      const version = versionOf(record?.draft?.version)
      const first = state === `draft`
        ? t(`publish.center.sentence.draft`, { version })
        : t(`publish.center.sentence.attention`, { version })
      return outdated ? [first, t(`publish.center.sentence.draftOutdated`)] : [first]
    }
    case `failed`:
      return [t(`publish.center.sentence.failed`, { reason: t(errorMessageKey(record?.errorCode)) }), ...previousDraft]
    case `login-required`:
      return [t(`publish.center.sentence.loginRequired`), ...previousDraft]
    case `published`: {
      const published = record?.published
      if (!published)
        return []
      const first = t(`publish.center.sentence.published`, { time: time(published.at), version: versionOf(published.version) })
      return outdated ? [first, t(`publish.center.sentence.publishedOutdated`)] : [first]
    }
    default:
      return [t(`publish.center.sentence.idle`)]
  }
})

const CHECK_ICON: Record<CheckLevel, { icon: typeof CheckIcon, tone: string }> = {
  ok: { icon: CheckIcon, tone: `bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-200` },
  warn: { icon: TriangleAlert, tone: `bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200` },
  bad: { icon: CircleX, tone: `bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200` },
}

function checkText(check: Check): string {
  if (check.key === `error`)
    return t(errorMessageKey(platformRecord.value?.errorCode))
  return t(`publish.center.checks.${check.key}`, check.params ?? {})
}

const canMarkPublished = computed(() =>
  Boolean(platformRecord.value?.draft) && !platformRecord.value?.published && props.view.state !== `queued` && props.view.state !== `syncing`,
)

function submitPublished() {
  const input = url.value.trim()
  const normalized = input ? normalizePublishedUrl(input) : null
  if (input && !normalized) {
    urlError.value = t(`publish.center.mark.invalidUrl`)
    return
  }
  publishStore.setPublished(props.postId, id.value, true, normalized ?? undefined)
  url.value = ``
  urlError.value = ``
}

function eventText(event: PublishEvent): string {
  const version = versionOf(event.version)
  switch (event.kind) {
    case `synced`:
      return t(`publish.center.history.synced`, { version })
    case `warning`:
      return t(`publish.center.history.warning`, { version })
    case `failed`:
      return t(`publish.center.history.failed`, { reason: t(errorMessageKey(event.errorCode)) })
    case `login-required`:
      return t(`publish.center.history.loginRequired`)
    case `published`:
      return t(`publish.center.history.published`, { version })
    default:
      return t(`publish.center.history.unpublished`)
  }
}

const diagnostics = computed(() => {
  const record = platformRecord.value
  if (!record)
    return ``
  const lines: string[] = [`status=${record.status}`]
  if (record.errorCode)
    lines.push(`error=${record.errorCode}`)
  if (record.report)
    lines.push(`method=${record.report.method} body=${record.report.bodyLength}/${record.report.expectedLength}`)
  if (record.detail)
    lines.push(record.detail)
  return lines.join(`\n`)
})
</script>

<template>
  <aside
    class="flex flex-col gap-5 rounded-lg border bg-background p-5"
    :aria-label="t('publish.center.detailLabel', { name: t(`publish.platforms.${id}`) })"
  >
    <div class="flex flex-wrap items-center gap-2">
      <h3 class="text-base font-semibold">
        {{ t(`publish.platforms.${id}`) }}
      </h3>
      <span class="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
        {{ t(`publish.format.${view.platform.format === 'wechat-html' ? 'wechatHtml' : view.platform.format}`) }}
      </span>
    </div>

    <div class="flex flex-col gap-3 rounded-md bg-muted/50 p-4">
      <PublishStatePill :view="view" />
      <p class="text-sm leading-relaxed">
        <span v-for="(sentence, index) in sentences" :key="index" class="me-1">{{ sentence }}</span>
      </p>

      <div class="flex flex-wrap gap-2">
        <template v-if="view.state === 'idle'">
          <Button size="sm" :disabled="!canSync" @click="emit('sync', id)">
            {{ t('publish.center.actions.sync') }}
          </Button>
        </template>
        <template v-else-if="view.state === 'syncing'">
          <Button size="sm" variant="outline" @click="emit('openTab', id)">
            {{ t('publish.center.actions.viewTab') }}
          </Button>
        </template>
        <template v-else-if="view.state === 'failed' || view.state === 'login-required'">
          <Button size="sm" :disabled="!canSync" @click="emit('sync', id)">
            {{ t('publish.center.actions.retry') }}
          </Button>
          <Button size="sm" variant="outline" @click="emit('openTab', id)">
            {{ t('publish.center.actions.viewTab') }}
          </Button>
        </template>
        <template v-else-if="view.state === 'draft' || view.state === 'attention'">
          <Button size="sm" @click="emit('openTab', id)">
            {{ t('publish.center.actions.openDraft') }}
          </Button>
          <Button size="sm" variant="outline" :disabled="!canSync" @click="emit('sync', id)">
            {{ t('publish.center.actions.resync') }}
          </Button>
        </template>
        <template v-else-if="view.state === 'published'">
          <Button v-if="platformRecord?.published?.url" size="sm" as-child>
            <a :href="platformRecord.published.url" target="_blank" rel="noopener noreferrer">
              {{ t('publish.center.actions.openArticle') }}
            </a>
          </Button>
          <Button size="sm" variant="ghost" @click="publishStore.setPublished(postId, id, false)">
            {{ t('publish.center.actions.unmark') }}
          </Button>
        </template>
      </div>

      <p v-if="view.state === 'published' && platformRecord?.published?.url" class="break-all text-xs text-muted-foreground">
        {{ platformRecord.published.url }}
      </p>
    </div>

    <section v-if="view.checks.length && platformRecord" class="flex flex-col gap-2">
      <h4 class="text-sm font-semibold">
        {{ t('publish.center.checksTitle') }}
        <span class="ms-1 text-xs font-normal text-muted-foreground">
          {{ t('publish.center.checksMeta', { time: time(platformRecord.at), version: versionOf(platformRecord.version) }) }}
        </span>
      </h4>
      <ul class="flex flex-col gap-2">
        <li v-for="check in view.checks" :key="check.key" class="flex items-start gap-2 text-sm">
          <span class="mt-0.5 inline-flex size-[18px] shrink-0 items-center justify-center rounded-full" :class="CHECK_ICON[check.level].tone" aria-hidden="true">
            <component :is="CHECK_ICON[check.level].icon" class="size-3" />
          </span>
          <span class="min-w-0">
            <span class="sr-only">{{ t(`publish.center.checkLevel.${check.level}`) }}</span>
            {{ checkText(check) }}
          </span>
        </li>
      </ul>
    </section>

    <form v-if="canMarkPublished" class="flex flex-col gap-2 border-t pt-4" novalidate @submit.prevent="submitPublished">
      <Label :for="`publish-center-url-${id}`" class="text-sm font-semibold">
        {{ t('publish.center.mark.label') }}
      </Label>
      <div class="flex flex-wrap gap-2">
        <Input
          :id="`publish-center-url-${id}`"
          v-model="url"
          type="url"
          inputmode="url"
          :placeholder="t('publish.center.mark.placeholder')"
          :aria-invalid="urlError ? 'true' : undefined"
          class="h-9 min-w-0 flex-[1_1_200px]"
        />
        <Button type="submit" size="sm">
          {{ t('publish.center.mark.submit') }}
        </Button>
      </div>
      <p v-if="urlError" class="text-xs text-destructive" role="alert">
        {{ urlError }}
      </p>
    </form>

    <section class="flex flex-col gap-2 border-t pt-4">
      <h4 class="text-sm font-semibold">
        {{ t('publish.center.history.title') }}
      </h4>
      <ol v-if="platformRecord?.history.length" class="flex flex-col gap-1.5">
        <li v-for="(event, index) in platformRecord.history" :key="index" class="flex gap-3 text-xs">
          <span class="min-w-12 shrink-0 tabular-nums text-muted-foreground">{{ time(event.at) }}</span>
          <span class="min-w-0">{{ eventText(event) }}</span>
        </li>
      </ol>
      <p v-else class="text-xs text-muted-foreground">
        {{ t('publish.center.history.empty') }}
      </p>
      <details v-if="diagnostics" class="text-xs">
        <summary class="cursor-pointer text-muted-foreground">
          {{ t('publish.center.diagnostics') }}
        </summary>
        <pre class="mt-2 whitespace-pre-wrap break-all rounded-md bg-muted p-3 font-mono text-muted-foreground">{{ diagnostics }}</pre>
      </details>
    </section>
  </aside>
</template>
