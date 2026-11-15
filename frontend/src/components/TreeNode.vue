<!-- One container of the feed tree, recursive (frontend/src/components/TreeNode.vue), R7.1-R7.3. -->
<script setup lang="ts">
import { inject } from 'vue'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useTree, type TreeItem } from '../stores/tree'
import type { Feed, Folder } from '../types'
import FeedIcon from './FeedIcon.vue'
import Icon from './Icon.vue'

export interface TreeContext {
  drag: { itemKey: string | null; intoFolderId: number | null }
  renaming: { key: string | null; value: string }
  pointerDown(event: PointerEvent, item: TreeItem): void
  clickFeed(feed: Feed): void
  clickFolder(folder: Folder): void
  feedMenu(event: MouseEvent, feed: Feed): void
  folderMenu(event: MouseEvent, folder: Folder): void
  commitRename(): void
  cancelRename(): void
  markRead(feed: Feed): void
  openSite(feed: Feed): void
}

defineProps<{ parentId: number | null; depth: number }>()
const tree = useTree()
const stories = useStories()
const settings = useSettings()
const ctx = inject<TreeContext>('tree')!

function key(item: TreeItem): string {
  return item.kind === 'feed' ? `feed:${item.feed.id}` : `folder:${item.folder.id}`
}
function feedSelected(feed: Feed): boolean {
  return stories.scope === `feed:${feed.id}` || stories.scope === `saved:feed:${feed.id}`
}
function folderSelected(folder: Folder): boolean {
  return stories.scope === `folder:${folder.id}` || stories.scope === `saved:folder:${folder.id}`
}
function errorTitle(feed: Feed): string {
  return `Error ${feed.exception_code}${feed.exception_message ? ': ' + feed.exception_message : ''} — right-click to change the address`
}
const vFocus = { mounted: (el: HTMLInputElement) => (el.focus(), el.select()) }
</script>

<template>
  <template v-for="item in tree.visibleChildren(parentId)" :key="key(item)">
    <template v-if="item.kind === 'folder'">
      <div
        class="tree-row folder"
        :class="{ selected: folderSelected(item.folder), 'drop-into': ctx.drag.intoFolderId === item.folder.id, dragging: ctx.drag.itemKey === key(item) }"
        :style="{ paddingLeft: 6 + depth * 14 + 'px' }"
        data-tree-kind="folder" :data-tree-id="item.folder.id"
        @pointerdown="ctx.pointerDown($event, item)"
        @click="ctx.clickFolder(item.folder)"
        @contextmenu.prevent="ctx.folderMenu($event, item.folder)"
      >
        <span class="arrow" :class="{ open: !item.folder.collapsed }" @click.stop="tree.setCollapsed(item.folder, !item.folder.collapsed)">
          <Icon name="chevron-right" />
        </span>
        <input
          v-if="ctx.renaming.key === key(item)" v-model="ctx.renaming.value" v-focus class="input inline-input"
          @click.stop @pointerdown.stop @keydown.enter.stop="ctx.commitRename()" @keydown.esc.stop="ctx.cancelRename()" @blur="ctx.commitRename()"
        />
        <span v-else class="title">{{ item.folder.title }}</span>
        <span
          v-if="(settings.prefs.folder_counts || item.folder.collapsed) && tree.folderCount(item.folder.id) > 0"
          class="count static" :class="{ saved: settings.prefs.sidebar_mode === 'saved' }"
        >{{ tree.folderCount(item.folder.id) }}</span>
      </div>
      <TreeNode v-if="!item.folder.collapsed" :parent-id="item.folder.id" :depth="depth + 1" />
    </template>
    <div
      v-else
      class="tree-row feed"
      :class="{ selected: feedSelected(item.feed), empty: tree.feedCount(item.feed) === 0, dragging: ctx.drag.itemKey === key(item) }"
      :style="{ paddingLeft: 14 + depth * 14 + 'px' }"
      data-tree-kind="feed" :data-tree-id="item.feed.id"
      @pointerdown="ctx.pointerDown($event, item)"
      @click="ctx.clickFeed(item.feed)"
      @dblclick="ctx.openSite(item.feed)"
      @contextmenu.prevent="ctx.feedMenu($event, item.feed)"
    >
      <FeedIcon :feed-id="item.feed.id" />
      <input
        v-if="ctx.renaming.key === key(item)" v-model="ctx.renaming.value" v-focus class="input inline-input"
        @click.stop @dblclick.stop @pointerdown.stop @keydown.enter.stop="ctx.commitRename()" @keydown.esc.stop="ctx.cancelRename()" @blur="ctx.commitRename()"
      />
      <span v-else class="title" :title="settings.prefs.show_tooltips ? item.feed.title : undefined">{{ item.feed.title }}</span>
      <span v-if="!item.feed.fetched_once && !item.feed.has_exception" class="spinner" title="Fetching…" />
      <span v-else-if="item.feed.has_exception" class="feed-state" :title="errorTitle(item.feed)"><Icon name="alert" /></span>
      <span v-if="item.feed.is_push" class="dim" title="Real-time updates"><Icon name="zap" style="width: 11px; height: 11px" /></span>
      <span v-if="settings.prefs.sidebar_mode === 'saved'" class="count saved static">{{ tree.feedCount(item.feed) }}</span>
      <span
        v-else-if="item.feed.muted" class="mute-state"
        :title="item.feed.unread ? `Muted — ${item.feed.unread} unread` : 'Muted'"
      ><Icon name="volume-off" /></span>
      <button
        v-else-if="item.feed.unread > 0" class="count" title="Mark as read"
        @click.stop="ctx.markRead(item.feed)" @dblclick.stop @pointerdown.stop
      >{{ item.feed.unread }}</button>
    </div>
  </template>
</template>
