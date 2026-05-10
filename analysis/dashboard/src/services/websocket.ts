import type { ConnectionStatus, TrajectoryPoint, SensorFrameData } from '../types'
import { bayesmech } from '../proto/bundle'
import {
  PREFIX_FRAME,
  PREFIX_ANNOTATION,
  PREFIX_PONGTOWN,
  decodeFrames,
  decodeAnnotations,
  decodePongtownRecords,
} from './proto'
import { legacyDashboardWsUrl, streamlogDashboardWsUrl } from './streamlog'

export type FrameListener = (frames: bayesmech.vision.PerceiverDataFrame[]) => void
export type AnnotationListener = (annotations: bayesmech.vision.SegmentationResponse[]) => void
export type PongtownListener = (records: bayesmech.vision.PongtownResponse[]) => void
export type StatsListener = (stats: Record<string, unknown>) => void
export type TrajectoryListener = (positions: TrajectoryPoint[]) => void
export type SensorDataListener = (frames: SensorFrameData[]) => void
type StatusListener = (status: ConnectionStatus) => void

class DashboardWebSocketService {
  private ws: WebSocket | null = null
  private frameListeners: Set<FrameListener> = new Set()
  private annotationListeners: Set<AnnotationListener> = new Set()
  private pongtownListeners: Set<PongtownListener> = new Set()
  private statsListeners: Set<StatsListener> = new Set()
  private trajectoryListeners: Set<TrajectoryListener> = new Set()
  private sensorDataListeners: Set<SensorDataListener> = new Set()
  private statusListeners: Set<StatusListener> = new Set()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private connectPromise: Promise<void> | null = null
  private connectAttempt = 0
  private intentionalClose = false
  private _status: ConnectionStatus = 'Disconnected'

  private setStatus(status: ConnectionStatus): void {
    this._status = status
    this.statusListeners.forEach((cb) => cb(status))
  }

  connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return
    }
    if (this.connectPromise) return

    this.intentionalClose = false
    this.setStatus('Connecting')
    const attempt = ++this.connectAttempt
    this.connectPromise = this.connectResolvedUrl(attempt).finally(() => {
      if (attempt === this.connectAttempt) {
        this.connectPromise = null
      }
    })
  }

  private async connectResolvedUrl(attempt: number): Promise<void> {
    const primaryUrl = await streamlogDashboardWsUrl()
    if (this.intentionalClose || attempt !== this.connectAttempt) return
    this.openSocket(primaryUrl, legacyDashboardWsUrl(), attempt)
  }

  private openSocket(url: string, fallbackUrl: string | undefined, attempt: number): void {
    if (this.intentionalClose || attempt !== this.connectAttempt) return
    let opened = false
    let fallbackStarted = false
    const ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      if (this.intentionalClose || attempt !== this.connectAttempt) {
        ws.close()
        return
      }
      opened = true
      this.setStatus('Connected')
    }

    ws.onmessage = (event: MessageEvent) => {
      if (this.intentionalClose || attempt !== this.connectAttempt) return
      if (event.data instanceof ArrayBuffer) {
        this.handleBinary(new Uint8Array(event.data))
      } else if (typeof event.data === 'string') {
        this.handleText(event.data)
      }
    }

    ws.onclose = () => {
      if (attempt !== this.connectAttempt) return
      if (!opened && fallbackUrl && !fallbackStarted && !this.intentionalClose) {
        fallbackStarted = true
        this.ws = null
        this.openSocket(fallbackUrl, undefined, attempt)
        return
      }
      this.setStatus('Disconnected')
      this.ws = null
      if (!this.intentionalClose) {
        this.scheduleReconnect()
      }
    }

    ws.onerror = () => {
      // onclose will fire after onerror
    }

    this.ws = ws
  }

  disconnect(): void {
    this.intentionalClose = true
    this.connectAttempt += 1
    this.connectPromise = null
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this.setStatus('Disconnected')
  }

  // ---- Send control messages ----

  getStats(): void {
    this.send({ action: 'get_stats' })
  }

  seek(start: number, end: number): void {
    this.send({ action: 'seek', start, end })
  }

  getAnnotations(): void {
    this.send({ action: 'get_annotations' })
  }

  getTrajectory(): void {
    this.send({ action: 'get_trajectory' })
  }

  getSensorData(): void {
    this.send({ action: 'get_sensor_data' })
  }

  getAnnotationForFrame(frameNumber: number): void {
    this.send({ action: 'get_annotations', frame_number: frameNumber })
  }

  getPongtownForFrame(frameNumber: number): void {
    this.send({ action: 'get_pongtown', frame_number: frameNumber })
  }

  getPongtownRange(start: number, end: number): void {
    this.send({ action: 'get_pongtown', start, end })
  }

  // ---- Listeners ----

  addFrameListener(cb: FrameListener): () => void {
    this.frameListeners.add(cb)
    return () => { this.frameListeners.delete(cb) }
  }

  addAnnotationListener(cb: AnnotationListener): () => void {
    this.annotationListeners.add(cb)
    return () => { this.annotationListeners.delete(cb) }
  }

  addPongtownListener(cb: PongtownListener): () => void {
    this.pongtownListeners.add(cb)
    return () => { this.pongtownListeners.delete(cb) }
  }

  addStatsListener(cb: StatsListener): () => void {
    this.statsListeners.add(cb)
    return () => { this.statsListeners.delete(cb) }
  }

  addTrajectoryListener(cb: TrajectoryListener): () => void {
    this.trajectoryListeners.add(cb)
    return () => { this.trajectoryListeners.delete(cb) }
  }

  addSensorDataListener(cb: SensorDataListener): () => void {
    this.sensorDataListeners.add(cb)
    return () => { this.sensorDataListeners.delete(cb) }
  }

  addStatusListener(cb: StatusListener): () => void {
    this.statusListeners.add(cb)
    cb(this._status)
    return () => { this.statusListeners.delete(cb) }
  }

  // ---- Internal ----

  private handleBinary(buf: Uint8Array): void {
    if (buf.length < 2) return
    const prefix = buf[0]
    const payload = buf.subarray(1)

    if (prefix === PREFIX_FRAME) {
      const frames = decodeFrames(payload)
      if (frames.length > 0) {
        this.frameListeners.forEach((cb) => cb(frames))
      }
    } else if (prefix === PREFIX_ANNOTATION) {
      const annotations = decodeAnnotations(payload)
      if (annotations.length > 0) {
        this.annotationListeners.forEach((cb) => cb(annotations))
      }
    } else if (prefix === PREFIX_PONGTOWN) {
      const records = decodePongtownRecords(payload)
      if (records.length > 0) {
        this.pongtownListeners.forEach((cb) => cb(records))
      }
    }
  }

  private handleText(data: string): void {
    try {
      const msg = JSON.parse(data)
      if (msg.type === 'stats') {
        this.statsListeners.forEach((cb) => cb(msg))
      } else if (msg.type === 'trajectory') {
        const positions = msg.positions as TrajectoryPoint[]
        this.trajectoryListeners.forEach((cb) => cb(positions))
      } else if (msg.type === 'sensor_data') {
        const frames = msg.frames as SensorFrameData[]
        this.sensorDataListeners.forEach((cb) => cb(frames))
      }
    } catch {
      // Ignore non-JSON
    }
  }

  private send(data: unknown): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, 3000)
  }
}

export const dashboardWs = new DashboardWebSocketService()
