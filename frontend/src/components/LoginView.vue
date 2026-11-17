<!-- Login and first-run setup form (frontend/src/components/LoginView.vue), item 22. The route is
     kept, so the requested page opens after login. The brand panel's rows are decoration: an
     abstract stream of stories drawn behind the logo. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { errorMessage } from '../api'
import { useSession } from '../stores/session'

const session = useSession()
const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const setup = computed(() => session.status === 'setup')

interface StreamRow {
  indent: string
  title: string
  meta: string
  unread: boolean
  hot: boolean
}

// Fixed pseudo-random widths, so the pattern is the same on every visit.
const streamRows: StreamRow[] = Array.from({ length: 34 }, (_, i) => ({
  indent: `${((i * 29) % 9) * 2}%`,
  title: `${30 + ((i * 37) % 45)}%`,
  meta: `${8 + ((i * 53) % 14)}%`,
  unread: (i * 7) % 5 !== 0,
  hot: i === 13 || i === 21,
}))

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await session.login(username.value, password.value, setup.value)
    if (route.name === 'login') router.replace('/')
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login">
    <section class="login-brand">
      <div class="login-stream" aria-hidden="true">
        <div v-for="(row, i) in streamRows" :key="i" class="login-row" :style="{ paddingLeft: row.indent }">
          <span class="login-dot" :class="{ unread: row.unread, hot: row.hot }" />
          <span class="login-bar" :class="{ hot: row.hot }" :style="{ width: row.title }" />
          <span class="login-bar meta" :style="{ width: row.meta }" />
        </div>
      </div>
      <div class="login-identity">
        <img src="/logo.svg" alt="" class="login-logo" />
        <div class="login-wordmark">NewsSharp</div>
        <p class="login-tagline">Every feed you follow, in one quiet stream. Hosted by you, shared with a few.</p>
      </div>
    </section>

    <section class="login-side">
      <form @submit.prevent="submit">
        <div class="login-heading">
          <h1>{{ setup ? 'Welcome' : 'Sign in' }}</h1>
          <p v-if="setup" class="login-notice">Create the first account. It becomes the administrator.</p>
          <p v-else class="dim">Pick up where you left off.</p>
        </div>
        <label class="login-field">
          <span>User name</span>
          <input v-model="username" class="input" autocomplete="username" autofocus required />
        </label>
        <label class="login-field">
          <span>Password</span>
          <input
            v-model="password" class="input" type="password" required
            :autocomplete="setup ? 'new-password' : 'current-password'" :minlength="setup ? 6 : undefined"
          />
        </label>
        <div v-if="error" class="error-text" role="alert">{{ error }}</div>
        <button class="btn primary" type="submit" :disabled="busy">
          {{ setup ? 'Create account' : 'Log in' }}
          <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
        </button>
        <p v-if="!setup" class="login-footnote dim">Accounts are created by the administrator of this server.</p>
      </form>
    </section>
  </div>
</template>
