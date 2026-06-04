// Small auth helpers shared across pages.

// Returns the Authorization header for authenticated requests.
// Spread into a fetch headers object: { ...authHeader() }
export function authHeader() {
  const token = localStorage.getItem("token")
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// If the backend rejected the token (expired / invalid), clear the session
// and bounce back to the login page. Returns true if it handled the response.
export function handleUnauthorized(response) {
  if (response.status === 401) {
    localStorage.clear()
    window.location.href = "/"
    return true
  }
  return false
}
