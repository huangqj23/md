<script setup lang="ts">
import type { PlatformState, PlatformView } from '@/services/publish/center'
import { Loader2 } from '@lucide/vue'

const props = defineProps<{
  view: PlatformView
}>()

const { t } = useI18n()

const TONE: Record<PlatformState, string> = {
  'queued': `bg-muted text-muted-foreground`,
  'syncing': `bg-muted text-foreground`,
  'draft': `bg-blue-100 text-blue-900 dark:bg-blue-900/40 dark:text-blue-100`,
  'attention': `bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100`,
  'failed': `bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-100`,
  'login-required': `bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-100`,
  'published': `bg-green-100 text-green-900 dark:bg-green-900/40 dark:text-green-100`,
  'idle': `bg-muted text-muted-foreground`,
}

const LABEL_KEY: Record<Exclude<PlatformState, `syncing`>, string> = {
  'queued': `queued`,
  'draft': `draft`,
  'attention': `attention`,
  'failed': `failed`,
  'login-required': `loginRequired`,
  'published': `published`,
  'idle': `idle`,
}

const label = computed(() => {
  const { state, live } = props.view
  if (state === `syncing`)
    return t(`publish.center.state.${live?.status === `opening` ? `opening` : `filling`}`)
  return t(`publish.center.state.${LABEL_KEY[state]}`)
})

const outdatedLabel = computed(() => {
  const { outdated, state, heldVersion } = props.view
  if (!outdated)
    return ``
  return state === `published`
    ? t(`publish.center.flags.publishedOld`)
    : t(`publish.center.flags.draftOld`, { version: heldVersion ?? `?` })
})
</script>

<template>
  <span class="inline-flex flex-wrap items-center gap-1.5">
    <span class="inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium" :class="TONE[view.state]">
      <Loader2 v-if="view.state === 'syncing'" class="size-3 animate-spin" aria-hidden="true" />
      {{ label }}
    </span>
    <span
      v-if="outdatedLabel"
      class="whitespace-nowrap rounded-full border border-orange-400 bg-orange-50 px-2 py-px text-xs text-orange-900 dark:border-orange-700 dark:bg-orange-950/50 dark:text-orange-100"
    >
      {{ outdatedLabel }}
    </span>
  </span>
</template>
