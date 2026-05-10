export type StreamlogPlane = 'outstream' | 'instream' | 'insightgen' | 'analyzers'

type StreamlogListener = {
  name?: string
  base_url?: string
  bind_host?: string
  bind_port?: number
  planes?: string[]
  health?: string
}

type StreamlogHealth = {
  status?: string
  listeners?: StreamlogListener[]
}

export type StreamlogEndpointHealth = {
  name: string
  kind: 'HTTP' | 'WebSocket'
  url: string
  ok: boolean
  detail: string
  latencyMs: number
}

const PLANE_NAMES: Record<StreamlogPlane, string> = {
  outstream: 'Outstream',
  instream: 'Instream',
  insightgen: 'Insightgen',
  analyzers: 'Analyzers',
}

const DEFAULT_BASE_URL = normalizeBaseUrl(import.meta.env.VITE_STREAMLOG_BASE_URL ?? '/streamlog')

const EXPLICIT_PLANE_URLS: Partial<Record<StreamlogPlane, string>> = {
  outstream: normalizeOptionalBaseUrl(import.meta.env.VITE_STREAMLOG_OUTSTREAM_URL),
  instream: normalizeOptionalBaseUrl(import.meta.env.VITE_STREAMLOG_INSTREAM_URL),
  insightgen: normalizeOptionalBaseUrl(import.meta.env.VITE_STREAMLOG_INSIGHTGEN_URL),
  analyzers: normalizeOptionalBaseUrl(import.meta.env.VITE_STREAMLOG_ANALYZERS_URL),
}

let discoveryPromise: Promise<{
  planeUrls: Partial<Record<StreamlogPlane, string>>
  health: StreamlogHealth | null
}> | null = null

function normalizeOptionalBaseUrl(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.trim() === '') return undefined
  return normalizeBaseUrl(value)
}

function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, '')
}

function joinUrl(base: string, path: string): string {
  const cleanPath = path.startsWith('/') ? path : `/${path}`
  if (base === '') return cleanPath
  return `${base}${cleanPath}`
}

function planeForName(name: string): StreamlogPlane | null {
  const normalized = name.toLowerCase()
  return (Object.keys(PLANE_NAMES) as StreamlogPlane[]).find((plane) => plane === normalized) ?? null
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
}

function browserCanUseAdvertisedBaseUrl(baseUrl: string): boolean {
  if (!baseUrl.startsWith('http://') && !baseUrl.startsWith('https://')) return true
  const advertised = new URL(baseUrl)
  return !isLoopbackHost(advertised.hostname) || isLoopbackHost(window.location.hostname)
}

function effectiveListenerBaseUrl(baseUrl: string): string {
  const normalized = normalizeBaseUrl(baseUrl)
  return browserCanUseAdvertisedBaseUrl(normalized) ? normalized : DEFAULT_BASE_URL
}

async function discoverStreamlog(): Promise<{
  planeUrls: Partial<Record<StreamlogPlane, string>>
  health: StreamlogHealth | null
}> {
  if (!discoveryPromise) {
    discoveryPromise = fetch(joinUrl(DEFAULT_BASE_URL, '/health'), { cache: 'no-store' })
      .then(async (res) => {
        if (!res.ok) return { planeUrls: {}, health: null }
        const health = await res.json() as StreamlogHealth
        const planeUrls: Partial<Record<StreamlogPlane, string>> = {}
        for (const listener of health.listeners ?? []) {
          if (!listener.base_url) continue
          const listenerBaseUrl = effectiveListenerBaseUrl(listener.base_url)
          for (const planeName of listener.planes ?? []) {
            const plane = planeForName(planeName)
            if (plane && !EXPLICIT_PLANE_URLS[plane]) {
              planeUrls[plane] = listenerBaseUrl
            }
          }
        }
        return { planeUrls, health }
      })
      .catch(() => ({ planeUrls: {}, health: null }))
  }
  return discoveryPromise
}

async function baseUrlForPlane(plane: StreamlogPlane): Promise<string> {
  const explicit = EXPLICIT_PLANE_URLS[plane]
  if (explicit) return explicit
  const discovered = await discoverStreamlog()
  return discovered.planeUrls[plane] ?? DEFAULT_BASE_URL
}

function baseUrlOrigin(baseUrl: string): string {
  if (!baseUrl.startsWith('http://') && !baseUrl.startsWith('https://')) return ''
  const parsed = new URL(baseUrl)
  return parsed.origin
}

function httpUrlToWsUrl(url: string): string {
  if (url.startsWith('https://')) return `wss://${url.slice('https://'.length)}`
  if (url.startsWith('http://')) return `ws://${url.slice('http://'.length)}`
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${url.startsWith('/') ? url : `/${url}`}`
}

export async function streamlogPlaneUrl(plane: StreamlogPlane, path: string): Promise<string> {
  return joinUrl(await baseUrlForPlane(plane), `/${plane}${path.startsWith('/') ? path : `/${path}`}`)
}

export function streamlogLegacyUrl(path: string): string {
  const explicitLegacyBase = normalizeOptionalBaseUrl(import.meta.env.VITE_STREAMLOG_LEGACY_BASE_URL)
  if (explicitLegacyBase) return joinUrl(explicitLegacyBase, path)
  return joinUrl(baseUrlOrigin(DEFAULT_BASE_URL), path)
}

export async function streamlogDashboardWsUrl(): Promise<string> {
  const explicit = normalizeOptionalBaseUrl(import.meta.env.VITE_STREAMLOG_DASHBOARD_WS_URL)
  if (explicit) return explicit
  return httpUrlToWsUrl(joinUrl(await baseUrlForPlane('outstream'), '/outstream/dashboard/ws'))
}

export function legacyDashboardWsUrl(): string {
  return httpUrlToWsUrl(joinUrl(baseUrlOrigin(DEFAULT_BASE_URL), '/ws/dashboard'))
}

async function checkHttpEndpoint(name: string, url: string): Promise<StreamlogEndpointHealth> {
  const started = performance.now()
  try {
    const res = await fetch(url, { cache: 'no-store' })
    return {
      name,
      kind: 'HTTP',
      url,
      ok: res.ok,
      detail: `${res.status} ${res.statusText || (res.ok ? 'OK' : 'Error')}`,
      latencyMs: Math.round(performance.now() - started),
    }
  } catch (error) {
    return {
      name,
      kind: 'HTTP',
      url,
      ok: false,
      detail: error instanceof Error ? error.message : 'request failed',
      latencyMs: Math.round(performance.now() - started),
    }
  }
}

async function checkWebSocketEndpoint(name: string, url: string): Promise<StreamlogEndpointHealth> {
  const started = performance.now()
  return new Promise((resolve) => {
    let settled = false
    const finish = (ok: boolean, detail: string, socket?: WebSocket) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      if (socket && socket.readyState === WebSocket.OPEN) socket.close()
      resolve({
        name,
        kind: 'WebSocket',
        url,
        ok,
        detail,
        latencyMs: Math.round(performance.now() - started),
      })
    }
    const timeout = window.setTimeout(() => finish(false, 'timeout'), 3000)
    try {
      const socket = new WebSocket(url)
      socket.onopen = () => {
        socket.send(JSON.stringify({ action: 'get_stats' }))
      }
      socket.onmessage = () => finish(true, 'stats response received', socket)
      socket.onerror = () => finish(false, 'connection error', socket)
      socket.onclose = () => finish(false, 'closed before response', socket)
    } catch (error) {
      finish(false, error instanceof Error ? error.message : 'connection failed')
    }
  })
}

export async function checkStreamlogEndpointHealth(): Promise<StreamlogEndpointHealth[]> {
  const [
    outstreamHealth,
    instreamHealth,
    insightgenHealth,
    analyzersHealth,
    recordings,
    pipelines,
    dashboardWs,
  ] = await Promise.all([
    streamlogPlaneUrl('outstream', '/health'),
    streamlogPlaneUrl('instream', '/health'),
    streamlogPlaneUrl('insightgen', '/health'),
    streamlogPlaneUrl('analyzers', '/health'),
    streamlogPlaneUrl('insightgen', '/recordings'),
    streamlogPlaneUrl('analyzers', '/pipelines'),
    streamlogDashboardWsUrl(),
  ])

  return Promise.all([
    checkHttpEndpoint('Streamlog health', joinUrl(DEFAULT_BASE_URL, '/health')),
    checkHttpEndpoint('Outstream health', outstreamHealth),
    checkHttpEndpoint('Instream health', instreamHealth),
    checkHttpEndpoint('Insightgen health', insightgenHealth),
    checkHttpEndpoint('Analyzers health', analyzersHealth),
    checkHttpEndpoint('Recordings library', recordings),
    checkHttpEndpoint('Analyzer pipelines', pipelines),
    checkHttpEndpoint('Legacy stream stats', streamlogLegacyUrl('/api/stream')),
    checkHttpEndpoint('Legacy playback status', streamlogLegacyUrl('/api/playback/status')),
    checkWebSocketEndpoint('Dashboard WebSocket', dashboardWs),
    checkWebSocketEndpoint('Legacy Dashboard WebSocket', legacyDashboardWsUrl()),
  ])
}
