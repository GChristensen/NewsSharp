<!-- Root component (frontend/src/App.vue): bootstrap, then the login form or the reader. -->
<script setup lang="ts">
import { onMounted } from 'vue'
import LoginView from './components/LoginView.vue'
import ReaderView from './components/ReaderView.vue'
import { useSession } from './stores/session'

const session = useSession()
onMounted(() => session.bootstrap())
</script>

<template>
  <div v-if="session.status === 'loading'" class="login"><span class="spinner" /></div>
  <div v-else-if="session.status === 'error'" class="login">
    <form @submit.prevent="session.bootstrap()">
      <h1>NewsSharp</h1>
      <div class="error-text">{{ session.error }}</div>
      <button class="btn primary" type="submit">Try again</button>
    </form>
  </div>
  <LoginView v-else-if="session.status === 'login' || session.status === 'setup'" />
  <ReaderView v-else />
</template>
