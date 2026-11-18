<!-- The video of a Grid card (frontend/src/components/CardVideo.vue), after NewsBlurMod 4d60cf40f.
     A muted, looping video laid over the card's image area. With `autoplay` (a story whose only media is a
     video) it plays while the card is on screen; otherwise it is created on the first hover over the cover
     image and plays while `hovered`. A failed load is retried once through the image proxy, after
     which the video is dropped and the cover image, if any, stays. -->
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { mediaFallback } from '../utils/media'
import Icon from './Icon.vue'

const props = defineProps<{ src: string; sigs: Record<string, string>; autoplay: boolean; hovered: boolean }>()

const video = ref<HTMLVideoElement | null>(null)
const created = ref(props.autoplay)
const playing = ref(false)
const failed = ref(false)
let onScreen = false
let observer: IntersectionObserver | null = null

function shouldPlay(): boolean {
  return props.autoplay ? onScreen : props.hovered
}

function sync() {
  const element = video.value
  if (!element) return
  if (shouldPlay()) {
    // Playback can be refused by the autoplay policy or interrupted by a pause; nothing to recover.
    element.play().catch(() => {})
  } else {
    element.pause()
    if (!props.autoplay) playing.value = false
  }
}

/** Stops playback and any download still in progress. */
function release() {
  observer?.disconnect()
  observer = null
  const element = video.value
  if (!element) return
  element.pause()
  element.removeAttribute('src')
  element.load()
}

function onError(event: Event) {
  if (mediaFallback(event, props.sigs)) {
    sync()
    return
  }
  release()
  failed.value = true
}

watch(
  () => props.hovered,
  (hovered) => {
    if (hovered) created.value = true
    sync()
  },
)

// Looping videos play only while their card is visible, so a long grid does not decode every clip at once.
watch(video, (element) => {
  observer?.disconnect()
  observer = null
  if (!element) return
  if (props.autoplay) {
    observer = new IntersectionObserver((entries) => {
      onScreen = entries[entries.length - 1].isIntersecting
      sync()
    })
    observer.observe(element)
  } else {
    sync()
  }
})

onBeforeUnmount(release)
</script>

<template>
  <span v-if="!autoplay && !failed" class="card-video-badge" :class="{ hidden: playing }"><Icon name="play" /></span>
  <video
    v-if="created && !failed" ref="video" class="card-video" :class="{ shown: autoplay || playing }" :src="src"
    muted loop playsinline :preload="autoplay ? 'metadata' : 'auto'" @playing="playing = true" @error="onError"
  />
</template>
