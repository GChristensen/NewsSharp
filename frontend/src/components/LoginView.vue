<!-- Login and first-run setup form (frontend/src/components/LoginView.vue), item 22. The route is
     kept, so the requested page opens after login. -->
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
    <form @submit.prevent="submit">
      <h1>NewsSharp</h1>
      <div v-if="setup" class="dim">Create the first account. It becomes the administrator.</div>
      <input v-model="username" class="input" placeholder="User name" autocomplete="username" autofocus required />
      <input
        v-model="password" class="input" type="password" placeholder="Password" required
        :autocomplete="setup ? 'new-password' : 'current-password'" :minlength="setup ? 6 : undefined"
      />
      <div v-if="error" class="error-text">{{ error }}</div>
      <button class="btn primary" type="submit" :disabled="busy">{{ setup ? 'Create account' : 'Log in' }}</button>
    </form>
  </div>
</template>
