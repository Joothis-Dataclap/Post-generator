const API_BASE =
  import.meta.env.VITE_API_BASE ?? 'http://localhost:8000/api/v1'

type ApiResult<T> = {
  ok: boolean
  data?: T
  error?: string
}

async function parseJson<T>(response: Response): Promise<ApiResult<T>> {
  if (response.ok) {
    const data = (await response.json()) as T
    return { ok: true, data }
  }

  let errorMessage = `Request failed (${response.status})`
  try {
    const errorBody = (await response.json()) as { detail?: string }
    if (errorBody?.detail) {
      errorMessage = errorBody.detail
    }
  } catch {
    // No body to parse
  }

  return { ok: false, error: errorMessage }
}

export async function postJson<T>(path: string, body: unknown) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  return parseJson<T>(response)
}

export async function getJson<T>(path: string) {
  const response = await fetch(`${API_BASE}${path}`)
  return parseJson<T>(response)
}

export async function postForm<T>(path: string, formData: FormData) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    body: formData,
  })

  return parseJson<T>(response)
}
