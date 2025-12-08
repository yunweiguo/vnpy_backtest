export type BacktestResponse = {
  run_id: string
  status: string
}

export async function callApi<T>(path: string, options: RequestInit = {}, base = ''): Promise<T> {
  const url = `${base || ''}${path}`
  const resp = await fetch(url, options)
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(text || resp.statusText)
  }
  return (await resp.json()) as T
}

export async function validateConfig(body: object, base: string) {
  return callApi<{ normalized_config: object; fingerprint: string; warnings: any[]; errors: any[] }>(
    '/strategies/validate',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    base,
  )
}

export async function submitBacktest(body: object, base: string) {
  return callApi<BacktestResponse>(
    '/backtests',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    base,
  )
}

export async function getBacktest(runId: string, base: string) {
  return callApi<{ status: string; message?: string }>(`/backtests/${runId}`, { method: 'GET' }, base)
}

export async function listArtifacts(runId: string, base: string) {
  return callApi<{ run_id: string; artifacts: string[] }>(`/backtests/${runId}/artifacts`, { method: 'GET' }, base)
}

export async function fetchArtifact(runId: string, filename: string, base: string, asText = false) {
  const url = `${base}/backtests/${runId}/artifacts/${filename}`
  const resp = await fetch(url)
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(text || resp.statusText)
  }
  return asText ? resp.text() : resp.json()
}

export async function resolveProfile(preset: string, base: string) {
  return callApi<{ normalized_config: object; warnings: any[]; fingerprint: string }>(
    '/reference/resolve-profile',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset }),
    },
    base,
  )
}

export async function diagnostics(params: URLSearchParams, base: string) {
  return callApi<any>(`/diagnostics/option-chain?${params.toString()}`, { method: 'GET' }, base)
}
