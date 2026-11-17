import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, TOKEN_KEY } from '../api/client'
import type { SystemStatus, User } from '../api/types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const user = ref<User | null>(null)
  const status = ref<SystemStatus | null>(null)

  function setToken(value: string | null) {
    token.value = value
    if (value) localStorage.setItem(TOKEN_KEY, value)
    else localStorage.removeItem(TOKEN_KEY)
  }

  async function login(identifier: string, password: string) {
    const result = await api.login(identifier, password)
    setToken(result.access_token)
    user.value = result.user
  }

  async function register(identifier: string, password: string, displayName: string) {
    const result = await api.register(identifier, password, displayName)
    setToken(result.access_token)
    user.value = result.user
  }

  async function restore() {
    if (!token.value) return false
    try {
      user.value = await api.me()
      return true
    } catch {
      setToken(null)
      return false
    }
  }

  async function loadStatus(force = false) {
    if (status.value && !force) return status.value
    status.value = await api.status()
    return status.value
  }

  function logout() {
    setToken(null)
    user.value = null
  }

  return { token, user, status, login, register, restore, loadStatus, logout, setToken }
})
