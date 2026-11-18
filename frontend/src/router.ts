// Routes (frontend/src/router.ts). Every route renders the reader; the scope is derived
// from the route. /subscribe/<url> is parsed from the raw location.
import { createRouter, createWebHistory, type RouteLocationNormalizedLoaded } from 'vue-router'

const Empty = { render: () => null }

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: Empty },
    { path: '/feed/:id(\\d+)', name: 'feed', component: Empty },
    { path: '/feed/:id(\\d+)/story/:storyId(\\d+)', name: 'feed-story', component: Empty },
    { path: '/folder/:id(\\d+)', name: 'folder', component: Empty },
    { path: '/folder/:id(\\d+)/story/:storyId(\\d+)', name: 'folder-story', component: Empty },
    { path: '/all', name: 'all', component: Empty },
    { path: '/all/story/:storyId(\\d+)', name: 'all-story', component: Empty },
    { path: '/saved', name: 'saved', component: Empty },
    { path: '/saved/tag/:tag', name: 'saved-tag', component: Empty },
    { path: '/saved/feed/:id(\\d+)', name: 'saved-feed', component: Empty },
    { path: '/saved/folder/:id(\\d+)', name: 'saved-folder', component: Empty },
    { path: '/subscribe/:url(.*)*', name: 'subscribe', component: Empty },
    { path: '/login', name: 'login', component: Empty },
    { path: '/:rest(.*)*', name: 'unknown', component: Empty },
  ],
})

export function scopeFromRoute(route: RouteLocationNormalizedLoaded): string | null {
  const name = String(route.name ?? '')
  const id = route.params.id
  if (name.startsWith('feed')) return `feed:${id}`
  if (name.startsWith('folder')) return `folder:${id}`
  if (name.startsWith('all')) return 'all'
  if (name === 'saved') return 'saved'
  if (name === 'saved-tag') return `saved:tag:${route.params.tag}`
  if (name === 'saved-feed') return `saved:feed:${id}`
  if (name === 'saved-folder') return `saved:folder:${id}`
  return null
}

export function scopePath(scope: string, storyId?: number | null): string {
  const parts = scope.split(':')
  let path: string
  if (parts[0] === 'saved') {
    if (parts.length === 1) path = '/saved'
    else if (parts[1] === 'tag') path = `/saved/tag/${encodeURIComponent(parts.slice(2).join(':'))}`
    else path = `/saved/${parts[1]}/${parts[2]}`
    return path
  }
  path = parts[0] === 'all' ? '/all' : `/${parts[0]}/${parts[1]}`
  return storyId ? `${path}/story/${storyId}` : path
}
