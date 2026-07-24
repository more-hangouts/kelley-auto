import api from './client'

export async function login(email, password) {
  const { data } = await api.post('/auth/login', { email, password })
  return data
}

export async function getMe() {
  const { data } = await api.get('/auth/me')
  return data
}

// D2: server-side logout bumps users.token_version so every token
// previously minted for this user becomes 401 on next request. Callers
// should not block local-state cleanup on the response — if the
// network drops, we still want the client to clear its stored token.
export async function logout() {
  await api.post('/auth/logout')
}

export async function changeOwnAdminPassword(currentPassword, newPassword) {
  await api.post('/admin/me/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}
