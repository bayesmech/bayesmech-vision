import {
  Activity,
  Camera,
  CarFront,
  ChevronDown,
  ChevronUp,
  CircleStop,
  Gauge,
  Plus,
  Radio,
  RefreshCw,
  Smartphone,
  TriangleAlert,
  Wifi,
  WifiOff,
} from 'lucide-react'
import {
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { ControlProjectManifest } from '../types'

type WheelId = 'lf' | 'rf' | 'lb' | 'rb'
type WheelState = Record<WheelId, number>

type RobotStatus = WheelState & {
  moving: boolean
  watchdog: boolean
  clients: number
  uptime_ms: number
}

type UltrasonicSample = {
  seq: number
  valid: boolean
  distance_mm: number
  filtered_mm: number
  duration_us: number
  age_ms: number
  interval_ms: number
  valid_samples: number
  invalid_samples: number
  timeouts: number
  trig_pin: number
  echo_pin: number
}

type DeviceSnapshot = {
  id: string
  name: string
  kind: 'robot' | 'mobile' | string
  connected: boolean
  last_seen_ms: number | null
  capabilities: {
    video: boolean
    drive: boolean
    ultrasonic: boolean
  }
  video: {
    path: string
    width: number
    height: number
    frame_count: number
    fps: number
  }
  status: RobotStatus | null
  ultrasonic: UltrasonicSample | null
  camera: {
    camera?: boolean
    rssi?: number
    stream_format?: string
    frame_width?: number
    frame_height?: number
  } | null
  error: string | null
}

type RobotProfile = {
  id: string
  name: string
  controllerUrl: string
  cameraUrl: string
}

const CONTROL_ENDPOINT_KEY = 'bayesmech:control-endpoint'
const DEFAULT_ENDPOINT = (
  import.meta.env.VITE_STREAMLOG_ENDPOINT || 'http://127.0.0.1:8080'
).replace(/\/+$/, '')
const STOPPED: WheelState = { lf: 0, rf: 0, lb: 0, rb: 0 }
const WHEELS: { id: WheelId; label: string }[] = [
  { id: 'lf', label: 'Left front' },
  { id: 'rf', label: 'Right front' },
  { id: 'lb', label: 'Left rear' },
  { id: 'rb', label: 'Right rear' },
]

function savedEndpoint() {
  return (localStorage.getItem(CONTROL_ENDPOINT_KEY) || DEFAULT_ENDPOINT).replace(/\/+$/, '')
}

function validEndpoint(value: string): string {
  const parsed = new URL(value.trim())
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('The Streamlog endpoint must use http or https.')
  }
  return parsed.toString().replace(/\/+$/, '')
}

async function responseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const payload = await response.json()
      detail = payload.detail || payload.error || detail
    } catch {
      // The status is still useful when a device returned non-JSON output.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

function deviceIcon(device: DeviceSnapshot) {
  return device.kind === 'robot' ? CarFront : Smartphone
}

function formatLastSeen(milliseconds: number | null) {
  if (milliseconds === null) return 'never seen'
  if (milliseconds < 1000) return 'now'
  if (milliseconds < 60_000) return `${Math.round(milliseconds / 1000)}s ago`
  return `${Math.round(milliseconds / 60_000)}m ago`
}

function projectDescription(project: ControlProjectManifest | null) {
  const deviceTypes = new Set(
    project?.devices.filter((device) => device.enabled).map((device) => device.deviceType) ?? [],
  )
  if (deviceTypes.size > 1) {
    return 'Multiple device video and telemetry streams captured together in this project.'
  }
  if (deviceTypes.has('PHONE_DEVICE')) {
    return 'Phone camera and mobile Perceiver sensor streams captured into this project.'
  }
  if (deviceTypes.has('ROBOT_HAND_DEVICE')) {
    return 'Robot hand actuators and video alongside augmented mobile Perceiver streams.'
  }
  if (deviceTypes.has('DRONE_DEVICE')) {
    return 'Drone control and video links alongside augmented mobile Perceiver streams.'
  }
  return 'Robot video, ultrasonic telemetry, and guarded motor control alongside mobile Perceiver streams.'
}

function projectTypeLabel(project: ControlProjectManifest) {
  if (project.projectType === 'CONTROL_PROJECT_TYPE_UNSPECIFIED') return 'DEVICE PROJECT'
  return project.projectType.replaceAll('_', ' ')
}

function VideoWall({
  endpoint,
  devices,
  selectedId,
  onSelect,
}: {
  endpoint: string
  devices: DeviceSnapshot[]
  selectedId: string | null
  onSelect: (deviceId: string) => void
}) {
  const [failedStreams, setFailedStreams] = useState<Record<string, boolean>>({})

  useEffect(() => setFailedStreams({}), [endpoint])

  if (devices.length === 0) {
    return (
      <div className="control-empty-streams">
        <Radio size={30} aria-hidden="true" />
        <strong>No live devices yet</strong>
        <span>Start the mobile stream or add a robot on the local network.</span>
      </div>
    )
  }

  return (
    <div className="device-video-grid" aria-label="Device video streams">
      {devices.map((device) => {
        const Icon = deviceIcon(device)
        const selected = device.id === selectedId
        const failed = failedStreams[device.id]
        return (
          <button
            type="button"
            className={`device-video-card${selected ? ' is-selected' : ''}`}
            key={device.id}
            onClick={() => onSelect(device.id)}
            aria-pressed={selected}
          >
            <div className="device-video-heading">
              <span>
                <Icon size={14} aria-hidden="true" />
                <strong>{device.name}</strong>
              </span>
              <span className={device.connected ? 'device-live-state is-online' : 'device-live-state'}>
                {device.connected ? <Wifi size={12} /> : <WifiOff size={12} />}
                {device.connected ? 'Live' : 'Offline'}
              </span>
            </div>
            <div className="device-video-frame">
              {device.capabilities.video && !failed ? (
                <img
                  src={`${endpoint}${device.video.path}?client=vision-control`}
                  alt={`Live stream from ${device.name}`}
                  onLoad={() => setFailedStreams((current) => ({ ...current, [device.id]: false }))}
                  onError={() => setFailedStreams((current) => ({ ...current, [device.id]: true }))}
                />
              ) : (
                <div className="device-video-placeholder">
                  <Camera size={26} aria-hidden="true" />
                  <span>{failed ? 'Video unavailable' : 'No video capability'}</span>
                </div>
              )}
            </div>
            <div className="device-video-meta">
              <span>{device.kind === 'robot' ? 'Robocar' : 'Perceiver mobile'}</span>
              <span>
                {device.video.fps > 0 ? `${device.video.fps.toFixed(1)} fps` : formatLastSeen(device.last_seen_ms)}
              </span>
            </div>
          </button>
        )
      })}
    </div>
  )
}

function ultrasonicColour(distanceCm: number) {
  if (distanceCm < 30) return '#ff6b6b'
  if (distanceCm < 80) return '#f0b35a'
  return '#62d2a2'
}

const ECHOGRAM_MAX_DISTANCE_MM = 2000

function Echogram({
  deviceId,
  sample,
}: {
  deviceId: string
  sample: UltrasonicSample | null
}) {
  const [history, setHistory] = useState<Array<{ seq: number; valid: boolean; distanceMm: number }>>([])
  const lastSequenceRef = useRef<number | null>(null)

  useEffect(() => {
    setHistory([])
    lastSequenceRef.current = null
  }, [deviceId])

  useEffect(() => {
    if (!sample || sample.seq === lastSequenceRef.current) return
    lastSequenceRef.current = sample.seq
    setHistory((current) => [
      ...current.slice(-119),
      { seq: sample.seq, valid: sample.valid, distanceMm: sample.filtered_mm },
    ])
  }, [sample])

  const fresh = Boolean(sample && sample.age_ms < 500)
  const valid = Boolean(sample?.valid && fresh)
  const distanceCm = valid ? Number(sample?.filtered_mm || 0) / 10 : null
  const points = history.map((item, index) => {
    const x = history.length <= 1 ? 100 : index / (history.length - 1) * 100
    const y = 94 - Math.min(ECHOGRAM_MAX_DISTANCE_MM, Math.max(0, item.distanceMm)) / ECHOGRAM_MAX_DISTANCE_MM * 84
    return { ...item, x, y }
  })
  const segments = points.flatMap((point, index) => {
    const previous = points[index - 1]
    if (!previous?.valid || !point.valid) return []
    return [{
      key: `${previous.seq}-${point.seq}`,
      x1: previous.x,
      y1: previous.y,
      x2: point.x,
      y2: point.y,
      colour: ultrasonicColour(Math.min(previous.distanceMm, point.distanceMm) / 10),
    }]
  })

  return (
    <section className="control-card echogram-card">
      <header className="control-card-heading">
        <span>
          <Activity size={15} aria-hidden="true" />
          Ultrasonic echogram
        </span>
        <span className={valid ? 'telemetry-state is-online' : 'telemetry-state'}>
          {valid ? 'Echo locked' : fresh ? 'No echo' : 'Stream stale'}
        </span>
      </header>
      <div className="echogram-body">
        <div className="echogram-reading">
          <span>Filtered range</span>
          <strong style={{ color: distanceCm === null ? undefined : ultrasonicColour(distanceCm) }}>
            {distanceCm === null ? '--' : distanceCm.toFixed(1)}
            <small>cm</small>
          </strong>
          <span>
            TRIG {sample?.trig_pin ?? '--'} · ECHO {sample?.echo_pin ?? '--'}
          </span>
          <span>{sample?.valid_samples ?? 0} hits · {sample?.timeouts ?? 0} missed</span>
        </div>
        <div className="echogram-chart">
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Ultrasonic history from zero to 200 centimetres">
            {[10, 31, 52, 73, 94].map((y, index) => (
              <g key={y}>
                <line x1="0" x2="100" y1={y} y2={y} className="echogram-grid-line" />
                <text x="1" y={y - 1.5}>{200 - index * 50}</text>
              </g>
            ))}
            {segments.map((segment) => (
              <line
                key={segment.key}
                x1={segment.x1}
                y1={segment.y1}
                x2={segment.x2}
                y2={segment.y2}
                className="echogram-line"
                stroke={segment.colour}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {points.filter((point) => point.valid).map((point) => (
              <circle
                key={point.seq}
                cx={point.x}
                cy={point.y}
                r="0.7"
                className="echogram-point"
                fill={ultrasonicColour(point.distanceMm / 10)}
              />
            ))}
            {points.filter((point) => !point.valid).map((point) => (
              <line
                key={point.seq}
                x1={point.x}
                x2={point.x}
                y1="91"
                y2="95"
                className="echogram-miss"
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
          <div className="echogram-axis"><span>history</span><span>now</span></div>
        </div>
      </div>
    </section>
  )
}

function describeDrive(keys: Set<string>) {
  const throttle = (keys.has('w') ? 1 : 0) - (keys.has('s') ? 1 : 0)
  const turn = (keys.has('a') ? 1 : 0) - (keys.has('d') ? 1 : 0)
  if (throttle > 0 && turn > 0) return 'Forward left'
  if (throttle > 0 && turn < 0) return 'Forward right'
  if (throttle < 0 && turn > 0) return 'Reverse left'
  if (throttle < 0 && turn < 0) return 'Reverse right'
  if (throttle > 0) return 'Forward'
  if (throttle < 0) return 'Reverse'
  if (turn > 0) return 'Pivot left'
  if (turn < 0) return 'Pivot right'
  return 'Stopped'
}

function DriveControls({
  endpoint,
  device,
  onCommand,
}: {
  endpoint: string
  device: DeviceSnapshot
  onCommand: () => void
}) {
  const [wheels, setWheels] = useState<WheelState>(STOPPED)
  const [driveSpeed, setDriveSpeed] = useState(160)
  const [driveMode, setDriveMode] = useState('Stopped')
  const [commandError, setCommandError] = useState<string | null>(null)
  const wheelsRef = useRef<WheelState>(STOPPED)
  const pressedKeysRef = useRef(new Set<string>())
  const requestInFlightRef = useRef(false)
  const requestPendingRef = useRef(false)
  const scheduleTimerRef = useRef<number | null>(null)

  const updateWheels = useCallback((next: WheelState) => {
    wheelsRef.current = next
    setWheels(next)
  }, [])

  const sendState = useCallback(async () => {
    if (requestInFlightRef.current) {
      requestPendingRef.current = true
      return
    }
    requestInFlightRef.current = true
    try {
      do {
        requestPendingRef.current = false
        const payload = { ...wheelsRef.current }
        try {
          const response = await fetch(`${endpoint}/api/devices/${encodeURIComponent(device.id)}/motors`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            cache: 'no-store',
          })
          await responseJson<RobotStatus>(response)
          setCommandError(null)
          onCommand()
        } catch (error) {
          setCommandError(error instanceof Error ? error.message : 'Motor command failed')
        }
      } while (requestPendingRef.current)
    } finally {
      requestInFlightRef.current = false
    }
  }, [device.id, endpoint, onCommand])

  const scheduleState = useCallback(() => {
    if (scheduleTimerRef.current !== null) window.clearTimeout(scheduleTimerRef.current)
    scheduleTimerRef.current = window.setTimeout(() => {
      scheduleTimerRef.current = null
      void sendState()
    }, 60)
  }, [sendState])

  const syncDriveKeys = useCallback((sendImmediately = true) => {
    const keys = pressedKeysRef.current
    const throttle = (keys.has('w') ? 1 : 0) - (keys.has('s') ? 1 : 0)
    const turn = (keys.has('a') ? 1 : 0) - (keys.has('d') ? 1 : 0)
    const clamp = (value: number) => Math.max(-driveSpeed, Math.min(driveSpeed, value))
    const left = clamp((throttle + turn) * driveSpeed)
    const right = clamp((throttle - turn) * driveSpeed)
    updateWheels({ lf: left, rf: right, lb: left, rb: right })
    setDriveMode(describeDrive(keys))
    if (sendImmediately) void sendState()
  }, [driveSpeed, sendState, updateWheels])

  const stopAll = useCallback((beacon = false) => {
    if (scheduleTimerRef.current !== null) window.clearTimeout(scheduleTimerRef.current)
    scheduleTimerRef.current = null
    pressedKeysRef.current.clear()
    updateWheels(STOPPED)
    setDriveMode('Stopped')
    const url = `${endpoint}/api/devices/${encodeURIComponent(device.id)}/stop`
    if (beacon && navigator.sendBeacon) {
      navigator.sendBeacon(url)
      return
    }
    void fetch(url, { method: 'POST', cache: 'no-store', keepalive: true })
      .then(responseJson<RobotStatus>)
      .then(() => {
        setCommandError(null)
        onCommand()
      })
      .catch((error) => setCommandError(error instanceof Error ? error.message : 'Stop failed'))
  }, [device.id, endpoint, onCommand, updateWheels])

  const pressKey = useCallback((key: string) => {
    if (pressedKeysRef.current.has(key)) return
    pressedKeysRef.current.add(key)
    syncDriveKeys()
  }, [syncDriveKeys])

  const releaseKey = useCallback((key: string) => {
    if (!pressedKeysRef.current.delete(key)) return
    syncDriveKeys()
  }, [syncDriveKeys])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (
        target?.matches('input, textarea, select, [contenteditable="true"]')
        || event.ctrlKey
        || event.metaKey
        || event.altKey
        || event.isComposing
      ) return
      const key = event.key.toLowerCase()
      if (!['w', 'a', 's', 'd'].includes(key)) return
      event.preventDefault()
      pressKey(key)
    }
    const handleKeyUp = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      if (!['w', 'a', 's', 'd'].includes(key)) return
      event.preventDefault()
      releaseKey(key)
    }
    const handleVisibility = () => {
      if (document.hidden) stopAll(true)
    }
    const handleBlur = () => stopAll()

    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('keyup', handleKeyUp)
    document.addEventListener('visibilitychange', handleVisibility)
    window.addEventListener('blur', handleBlur)
    const watchdogRefresh = window.setInterval(() => {
      if (Object.values(wheelsRef.current).some((value) => value !== 0)) void sendState()
    }, 350)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('keyup', handleKeyUp)
      document.removeEventListener('visibilitychange', handleVisibility)
      window.removeEventListener('blur', handleBlur)
      window.clearInterval(watchdogRefresh)
      stopAll(true)
    }
  }, [pressKey, releaseKey, sendState, stopAll])

  useEffect(() => {
    if (pressedKeysRef.current.size > 0) {
      syncDriveKeys(false)
      scheduleState()
    }
  }, [driveSpeed, scheduleState, syncDriveKeys])

  const pointerDown = (key: string) => (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    pressKey(key)
  }
  const pointerUp = (key: string) => (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    releaseKey(key)
  }

  return (
    <div className="robot-control-grid">
      <section className="control-card drive-card">
        <header className="control-card-heading">
          <span><Gauge size={15} aria-hidden="true" />Hold to drive · WASD</span>
          <strong>{driveMode}</strong>
        </header>
        <div className="drive-control-body">
          <div className="drive-keypad" aria-label="Directional robot controls">
            {[
              ['w', 'Forward', ChevronUp],
              ['a', 'Left', ChevronDown],
              ['s', 'Reverse', ChevronDown],
              ['d', 'Right', ChevronUp],
            ].map(([key, label, Icon]) => (
              <button
                type="button"
                data-key={key as string}
                className={pressedKeysRef.current.has(key as string) ? 'drive-key is-active' : 'drive-key'}
                key={key as string}
                onPointerDown={pointerDown(key as string)}
                onPointerUp={pointerUp(key as string)}
                onPointerCancel={pointerUp(key as string)}
                onLostPointerCapture={() => releaseKey(key as string)}
                onContextMenu={(event) => event.preventDefault()}
                aria-pressed={pressedKeysRef.current.has(key as string)}
              >
                <kbd>{String(key).toUpperCase()}</kbd>
                <span>{label as string}</span>
                <Icon size={12} aria-hidden="true" />
              </button>
            ))}
          </div>
          <div className="drive-primary-actions">
            <label>
              <span>Drive speed</span>
              <strong>{driveSpeed}</strong>
              <input
                type="range"
                min="1"
                max="255"
                value={driveSpeed}
                onChange={(event) => setDriveSpeed(Number(event.target.value))}
              />
            </label>
            <button type="button" className="emergency-stop" onClick={() => stopAll()}>
              <CircleStop size={18} aria-hidden="true" />
              Emergency stop
            </button>
          </div>
        </div>
        {commandError ? (
          <div className="control-inline-error">
            <TriangleAlert size={13} aria-hidden="true" />
            {commandError}
          </div>
        ) : null}
      </section>

      <section className="wheel-control-grid" aria-label="Independent wheel controls">
        {WHEELS.map(({ id, label }) => (
          <label className="control-card wheel-control" key={id}>
            <span><strong>{label}</strong><output>{wheels[id]}</output></span>
            <input
              type="range"
              min="-255"
              max="255"
              value={wheels[id]}
              onChange={(event) => {
                pressedKeysRef.current.clear()
                setDriveMode('Manual wheel control')
                updateWheels({ ...wheelsRef.current, [id]: Number(event.target.value) })
                scheduleState()
              }}
              onPointerUp={() => void sendState()}
            />
            <small><span>Reverse</span><span>Stop</span><span>Forward</span></small>
          </label>
        ))}
      </section>
    </div>
  )
}

function AddRobotForm({
  endpoint,
  onAdded,
  onCancel,
}: {
  endpoint: string
  onAdded: (device: DeviceSnapshot) => void
  onCancel: () => void
}) {
  const [profile, setProfile] = useState<RobotProfile>({
    id: 'robocar-2',
    name: 'Robocar 2',
    controllerUrl: 'http://192.168.1.41',
    cameraUrl: 'http://192.168.1.42',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const response = await fetch(`${endpoint}/api/devices/robots`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profile),
      })
      onAdded(await responseJson<DeviceSnapshot>(response))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not add robot')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="add-device-form" onSubmit={submit}>
      <div className="add-device-form-heading">
        <div>
          <span className="eyebrow">Network device</span>
          <h3>Add a Robocar</h3>
        </div>
        <button type="button" className="toolbar-button" onClick={onCancel}>Cancel</button>
      </div>
      <div className="add-device-fields">
        <label>
          <span>Device id</span>
          <input
            required
            value={profile.id}
            onChange={(event) => setProfile((current) => ({ ...current, id: event.target.value }))}
            placeholder="robocar-2"
          />
        </label>
        <label>
          <span>Display name</span>
          <input
            required
            value={profile.name}
            onChange={(event) => setProfile((current) => ({ ...current, name: event.target.value }))}
            placeholder="Workshop car"
          />
        </label>
        <label>
          <span>Controller URL</span>
          <input
            required
            type="url"
            value={profile.controllerUrl}
            onChange={(event) => setProfile((current) => ({ ...current, controllerUrl: event.target.value }))}
            placeholder="http://192.168.1.31"
          />
        </label>
        <label>
          <span>Camera URL</span>
          <input
            required
            type="url"
            value={profile.cameraUrl}
            onChange={(event) => setProfile((current) => ({ ...current, cameraUrl: event.target.value }))}
            placeholder="http://192.168.1.32"
          />
        </label>
      </div>
      {error ? <div className="control-inline-error"><TriangleAlert size={13} />{error}</div> : null}
      <button type="submit" className="add-device-submit" disabled={submitting}>
        {submitting ? <RefreshCw className="spin" size={14} /> : <Plus size={14} />}
        {submitting ? 'Connecting…' : 'Add robot'}
      </button>
    </form>
  )
}

export default function ControlPanel({
  controlProject,
}: {
  controlProject: ControlProjectManifest | null
}) {
  const [endpoint, setEndpoint] = useState(savedEndpoint)
  const [endpointDraft, setEndpointDraft] = useState(endpoint)
  const [devices, setDevices] = useState<DeviceSnapshot[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  const [sessionState, setSessionState] = useState<string | null>(null)
  const [showAddRobot, setShowAddRobot] = useState(false)
  const [refreshToken, setRefreshToken] = useState(0)
  const requestInFlightRef = useRef(false)

  const loadDevices = useCallback(async () => {
    if (requestInFlightRef.current) return
    requestInFlightRef.current = true
    try {
      const response = await fetch(`${endpoint}/api/devices`, { cache: 'no-store' })
      const payload = await responseJson<{ devices: DeviceSnapshot[] }>(response)
      const projectDeviceIds = new Set(
        controlProject?.devices.filter((device) => device.enabled).map((device) => device.deviceId)
        ?? [],
      )
      const visibleDevices = controlProject
        ? payload.devices.filter((device) => (
            projectDeviceIds.has(device.id) || device.kind === 'mobile'
          ))
        : payload.devices
      setDevices(visibleDevices)
      setConnectionError(null)
      setSelectedId((current) => {
        if (current && visibleDevices.some((device) => device.id === current)) return current
        return visibleDevices.find((device) => device.kind === 'robot')?.id
          ?? visibleDevices[0]?.id
          ?? null
      })
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : 'Streamlog is unavailable')
    } finally {
      requestInFlightRef.current = false
    }
  }, [controlProject, endpoint])

  useEffect(() => {
    void loadDevices()
    const timer = window.setInterval(() => void loadDevices(), 250)
    return () => window.clearInterval(timer)
  }, [loadDevices, refreshToken])

  useEffect(() => {
    if (!controlProject) return
    let cancelled = false
    setSessionState('Opening project capture…')
    void fetch(`${endpoint}/api/control-projects/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ manifest_path: controlProject.manifestPath }),
      cache: 'no-store',
    })
      .then(responseJson<{ project_id: string; active: boolean }>)
      .then(() => {
        if (!cancelled) setSessionState('Recording device streams into this project')
      })
      .catch((error) => {
        if (!cancelled) {
          setSessionState(
            `Project capture unavailable: ${error instanceof Error ? error.message : 'unknown error'}`,
          )
        }
      })
    return () => {
      cancelled = true
    }
  }, [controlProject, endpoint])

  const selected = useMemo(
    () => devices.find((device) => device.id === selectedId) ?? null,
    [devices, selectedId],
  )

  const connectEndpoint = (event: FormEvent) => {
    event.preventDefault()
    try {
      const next = validEndpoint(endpointDraft)
      localStorage.setItem(CONTROL_ENDPOINT_KEY, next)
      setEndpoint(next)
      setConnectionError(null)
      setDevices([])
      setRefreshToken((current) => current + 1)
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : 'Invalid endpoint')
    }
  }

  return (
    <div className="control-panel">
      <header className="control-panel-header">
        <div>
          <span className="eyebrow">Live device fleet</span>
          <h2>{controlProject?.displayName ?? 'Control'}</h2>
          <p>{projectDescription(controlProject)}</p>
        </div>
        <div className="control-panel-actions">
          <form className="control-endpoint" onSubmit={connectEndpoint}>
            <label htmlFor="control-endpoint">Streamlog</label>
            <input
              id="control-endpoint"
              type="url"
              value={endpointDraft}
              onChange={(event) => setEndpointDraft(event.target.value)}
              aria-label="Streamlog server endpoint"
            />
            <button type="submit">Connect</button>
          </form>
          {!controlProject || controlProject.projectType === 'ROBOT_CAR' ? (
            <button
              type="button"
              className="toolbar-button"
              onClick={() => setShowAddRobot((current) => !current)}
            >
              <Plus size={14} aria-hidden="true" />
              Robot network
            </button>
          ) : null}
          <button
            type="button"
            className="icon-button"
            onClick={() => {
              setRefreshToken((current) => current + 1)
              void loadDevices()
            }}
            aria-label="Refresh devices"
            title="Refresh devices"
          >
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>
      </header>

      {controlProject ? (
        <div className="control-project-topology">
          <span>{projectTypeLabel(controlProject)}</span>
          {controlProject.devices.filter((device) => device.enabled).map((device) => (
            <span key={device.deviceId}>
              {device.displayName} · control {device.controlPort || '—'} · stream {device.streamPort || '—'}
            </span>
          ))}
          {sessionState ? (
            <strong className={sessionState.startsWith('Project capture unavailable') ? 'is-error' : ''}>
              {sessionState}
            </strong>
          ) : null}
        </div>
      ) : null}

      {connectionError ? (
        <div className="control-server-error">
          <WifiOff size={16} aria-hidden="true" />
          <span><strong>Streamlog unavailable.</strong> {connectionError}</span>
        </div>
      ) : (
        <div className="control-server-state">
          <Wifi size={13} aria-hidden="true" />
          {devices.filter((device) => device.connected).length} of {devices.length} devices online
        </div>
      )}

      {showAddRobot ? (
        <AddRobotForm
          endpoint={endpoint}
          onAdded={(device) => {
            setShowAddRobot(false)
            setSelectedId(device.id)
            setRefreshToken((current) => current + 1)
          }}
          onCancel={() => setShowAddRobot(false)}
        />
      ) : null}

      <VideoWall
        endpoint={endpoint}
        devices={devices}
        selectedId={selectedId}
        onSelect={setSelectedId}
      />

      {selected ? (
        <section className="selected-device">
          <div className="selected-device-heading">
            <div>
              <span className={selected.connected ? 'selected-device-dot is-online' : 'selected-device-dot'} />
              <div>
                <h3>{selected.name}</h3>
                <p>{selected.id} · {selected.kind} · {formatLastSeen(selected.last_seen_ms)}</p>
              </div>
            </div>
            <div className="selected-device-metrics">
              <span><Camera size={13} />{selected.video.frame_count.toLocaleString()} frames</span>
              {selected.camera?.rssi !== undefined ? <span><Wifi size={13} />{selected.camera.rssi} dBm</span> : null}
              {selected.status ? <span><Gauge size={13} />{selected.status.clients} clients</span> : null}
            </div>
          </div>

          {selected.error ? (
            <div className="control-inline-error">
              <TriangleAlert size={13} aria-hidden="true" />
              {selected.error}
            </div>
          ) : null}

          {selected.capabilities.drive ? (
            <>
              <Echogram deviceId={selected.id} sample={selected.ultrasonic} />
              <DriveControls
                key={`${endpoint}:${selected.id}`}
                endpoint={endpoint}
                device={selected}
                onCommand={loadDevices}
              />
              <p className="control-safety-note">
                Commands refresh every 350 ms while moving. Releasing WASD, changing tabs, hiding the app,
                or losing focus sends a stop; the robot’s one-second hardware watchdog remains authoritative.
              </p>
            </>
          ) : (
            <div className="mobile-device-note">
              <Smartphone size={20} aria-hidden="true" />
              <div>
                <strong>Perceiver stream</strong>
                <span>This device publishes video and sensor frames but exposes no drive controls.</span>
              </div>
            </div>
          )}
        </section>
      ) : null}
    </div>
  )
}
