import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { LoaderCircle, MapPinned, Route } from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type {
  GpsSample,
  IdoSlamCenterlinePoint,
  IdoSlamPose,
  IdoSlamSummary,
  SensorDataSummary,
} from '../types'

type MapGenerationPanelProps = {
  currentFrameIndex: number
  sourcePath?: string
  artifactPath?: string
  getSensorData: (sourcePath?: string) => Promise<SensorDataSummary | null>
  getIdoSlamData: (artifactPath?: string) => Promise<IdoSlamSummary | null>
}

type Point2 = { x: number; y: number }

const PLOT_WIDTH = 720
const PLOT_HEIGHT = 340

function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

function gpsAt(data: SensorDataSummary | null, frameIndex: number): GpsSample | null {
  if (!data) return null
  for (let index = Math.min(frameIndex, data.samples.length - 1); index >= 0; index -= 1) {
    if (data.samples[index]?.gps) return data.samples[index].gps
  }
  return data.samples.find((sample) => sample.gps)?.gps ?? null
}

function fitPoints(points: Point2[], padding = 24): (point: Point2) => Point2 {
  if (!points.length) return (point) => point
  const xs = points.map((point) => point.x)
  const ys = points.map((point) => point.y)
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const spanX = Math.max(maxX - minX, 0.001)
  const spanY = Math.max(maxY - minY, 0.001)
  const scale = Math.min((PLOT_WIDTH - padding * 2) / spanX, (PLOT_HEIGHT - padding * 2) / spanY)
  const offsetX = (PLOT_WIDTH - spanX * scale) / 2
  const offsetY = (PLOT_HEIGHT - spanY * scale) / 2
  return (point) => ({
    x: offsetX + (point.x - minX) * scale,
    y: PLOT_HEIGHT - (offsetY + (point.y - minY) * scale),
  })
}

function pointString(points: Point2[], map: (point: Point2) => Point2): string {
  return points.map((point) => {
    const mapped = map(point)
    return `${mapped.x.toFixed(1)},${mapped.y.toFixed(1)}`
  }).join(' ')
}

function poseProjection(poses: IdoSlamPose[]): Point2[] {
  if (!poses.length) return []
  const axes = (['x', 'y', 'z'] as const)
    .map((axis) => {
      const values = poses.map((pose) => pose.position[axis])
      return { axis, span: Math.max(...values) - Math.min(...values) }
    })
    .sort((left, right) => right.span - left.span)
  return poses.map((pose) => ({ x: pose.position[axes[0].axis], y: pose.position[axes[1].axis] }))
}

function currentPoseIndex(poses: IdoSlamPose[], frameIndex: number): number {
  if (!poses.length) return -1
  let low = 0
  let high = poses.length - 1
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (poses[middle].frameIndex < frameIndex) low = middle + 1
    else high = middle
  }
  if (low === 0) return 0
  const previous = low - 1
  return Math.abs(poses[low].frameIndex - frameIndex)
    < Math.abs(poses[previous].frameIndex - frameIndex)
    ? low
    : previous
}

function RoutePlot({ title, poses, currentFrameIndex, accent }: {
  title: string
  poses: IdoSlamPose[]
  currentFrameIndex: number
  accent: string
}) {
  const geometry = useMemo(() => {
    const points = poseProjection(poses)
    const map = fitPoints(points)
    return {
      points,
      map,
      path: pointString(points, map),
    }
  }, [poses])
  const activeIndex = currentPoseIndex(poses, currentFrameIndex)
  const active = activeIndex >= 0
    ? geometry.map(geometry.points[activeIndex])
    : null
  return (
    <section className="map-plot-card">
      <header><strong>{title}</strong><span>{compactNumber(poses.length)} poses</span></header>
      <svg viewBox={`0 0 ${PLOT_WIDTH} ${PLOT_HEIGHT}`} role="img" aria-label={title}>
        <rect className="map-plot-background" width={PLOT_WIDTH} height={PLOT_HEIGHT} />
        <path className="plot-grid-line" d="M0 85H720M0 170H720M0 255H720M180 0V340M360 0V340M540 0V340" />
        {geometry.points.length > 1 && <polyline points={geometry.path} fill="none" stroke={accent} strokeWidth="2.5" vectorEffect="non-scaling-stroke" />}
        {active && <circle cx={active.x} cy={active.y} r="7" className="map-current-point" />}
        {!geometry.points.length && <text className="map-empty-label" x={PLOT_WIDTH / 2} y={PLOT_HEIGHT / 2} textAnchor="middle">No IDOSLAM pose data</text>}
      </svg>
    </section>
  )
}

const CenterlinePlot = memo(function CenterlinePlot({
  points,
}: {
  points: IdoSlamCenterlinePoint[]
}) {
  const geometry = useMemo(() => {
    const center = points.map((point) => ({ x: point.centerX, y: point.centerY }))
    const left = points.map((point) => ({ x: point.leftX, y: point.leftY }))
    const right = points.map((point) => ({ x: point.rightX, y: point.rightY }))
    const map = fitPoints([...left, ...right, ...center])
    return {
      center: pointString(center, map),
      left: pointString(left, map),
      right: pointString(right, map),
    }
  }, [points])
  return (
    <section className="map-plot-card">
      <header><strong>Canonical road model</strong><span>{compactNumber(points.length)} centerline bins</span></header>
      <svg viewBox={`0 0 ${PLOT_WIDTH} ${PLOT_HEIGHT}`} role="img" aria-label="Canonical road centerline and boundaries">
        <rect className="map-plot-background" width={PLOT_WIDTH} height={PLOT_HEIGHT} />
        <path className="plot-grid-line" d="M0 85H720M0 170H720M0 255H720M180 0V340M360 0V340M540 0V340" />
        {points.length > 1 && (
          <>
            <polyline points={geometry.left} fill="none" stroke="#d7687d" strokeWidth="2" />
            <polyline points={geometry.center} fill="none" stroke="#eef0f3" strokeWidth="2.5" />
            <polyline points={geometry.right} fill="none" stroke="#62d2a2" strokeWidth="2" />
          </>
        )}
        {!points.length && <text className="map-empty-label" x={PLOT_WIDTH / 2} y={PLOT_HEIGHT / 2} textAnchor="middle">No canonical centerline data</text>}
      </svg>
    </section>
  )
})

function GpsRouteMap({ points, current }: { points: GpsSample[]; current: GpsSample | null }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const routeRef = useRef<L.Polyline | null>(null)
  const markerRef = useRef<L.CircleMarker | null>(null)

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = L.map(containerRef.current, { center: [0, 0], zoom: 2, attributionControl: true })
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map)
    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
      routeRef.current = null
      markerRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !points.length) return
    const positions = points.map((point) => [point.latitude, point.longitude] as L.LatLngTuple)
    if (routeRef.current) routeRef.current.setLatLngs(positions)
    else routeRef.current = L.polyline(positions, { color: '#5aa9e6', weight: 3, opacity: 0.82 }).addTo(map)
    if (positions.length === 1) map.setView(positions[0], 17)
    else map.fitBounds(L.latLngBounds(positions), { padding: [24, 24], maxZoom: 17 })
  }, [points])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !current) return
    const position: L.LatLngTuple = [current.latitude, current.longitude]
    if (markerRef.current) markerRef.current.setLatLng(position)
    else markerRef.current = L.circleMarker(position, {
      radius: 7,
      color: '#f0b35a',
      fillColor: '#f0b35a',
      fillOpacity: 1,
      weight: 2,
    }).addTo(map)
    const stableViewport = map.getBounds().pad(-0.16)
    if (!stableViewport.contains(position)) {
      map.panTo(position, { animate: true, duration: 0.18 })
    }
  }, [current])

  return (
    <section className="gps-map-card">
      <header>
        <div><MapPinned size={16} aria-hidden="true" /><strong>GPS map</strong></div>
        <span>{current ? `${current.latitude.toFixed(6)}, ${current.longitude.toFixed(6)} · ±${current.accuracy.toFixed(1)} m` : 'No GPS fix'}</span>
      </header>
      <div className="gps-map-canvas" ref={containerRef} />
    </section>
  )
}

export default function MapGenerationPanel({
  currentFrameIndex,
  sourcePath,
  artifactPath,
  getSensorData,
  getIdoSlamData,
}: MapGenerationPanelProps) {
  const [sensors, setSensors] = useState<SensorDataSummary | null>(null)
  const [slam, setSlam] = useState<IdoSlamSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.allSettled([getSensorData(sourcePath), getIdoSlamData(artifactPath)])
      .then(([sensorResult, slamResult]) => {
        if (cancelled) return
        if (sensorResult.status === 'fulfilled') setSensors(sensorResult.value)
        if (slamResult.status === 'fulfilled') setSlam(slamResult.value)
        const failures = [sensorResult, slamResult]
          .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
          .map((result) => result.reason instanceof Error ? result.reason.message : String(result.reason))
        if (failures.length) setError(failures.join(' · '))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [artifactPath, getIdoSlamData, getSensorData, sourcePath])

  const gpsPoints = useMemo(
    () => sensors?.samples.flatMap((sample) => sample.gps ? [sample.gps] : []) ?? [],
    [sensors],
  )
  const currentGps = gpsAt(sensors, currentFrameIndex)

  if (loading && !sensors && !slam) {
    return <div className="sensor-panel-state"><LoaderCircle className="sensor-loading-icon" size={22} aria-hidden="true" /><span>Loading GPS and IDOSLAM maps…</span></div>
  }

  return (
    <div className="map-generation-panel">
      <div className="sensor-panel-heading">
        <div>
          <span className="eyebrow">Geospatial + visual odometry</span>
          <h2>Map Generation</h2>
          <p>The GPS route and IDOSLAM pipeline stay together here, separate from the raw sensor charts.</p>
        </div>
        <div className="sensor-frame-chip">
          <Route size={15} aria-hidden="true" />
          <span>Frame {currentFrameIndex + 1}</span>
          <strong>{gpsPoints.length ? `${compactNumber(gpsPoints.length)} GPS fixes` : 'No GPS'}</strong>
        </div>
      </div>

      {error && <div className="map-inline-error">{error}</div>}
      <GpsRouteMap points={gpsPoints} current={currentGps} />

      <div className="map-metrics-grid">
        <div><span>Raw poses</span><strong>{compactNumber(slam?.framePoses.length ?? 0)}</strong></div>
        <div><span>GPS-refined poses</span><strong>{compactNumber(slam?.refinedFramePoses.length ?? 0)}</strong></div>
        <div><span>Ground points</span><strong>{compactNumber(slam?.groundPointCount ?? 0)}</strong></div>
        <div><span>SIFT correspondences</span><strong>{compactNumber(slam?.correspondenceCount ?? 0)}</strong></div>
        <div><span>Inlier correspondences</span><strong>{compactNumber(slam?.inlierCount ?? 0)}</strong></div>
        <div><span>Width estimates</span><strong>{compactNumber((slam?.planeWidthEstimates.length ?? 0) + (slam?.triangulatedWidthEstimates.length ?? 0))}</strong></div>
      </div>

      <div className="map-plot-grid">
        <RoutePlot title="Pre-GPS optimization map" poses={slam?.framePoses ?? []} currentFrameIndex={currentFrameIndex} accent="#f0b35a" />
        <RoutePlot title="Post-GPS optimization map" poses={slam?.refinedFramePoses ?? []} currentFrameIndex={currentFrameIndex} accent="#5aa9e6" />
        <CenterlinePlot points={slam?.canonicalCenterline ?? []} />
        <section className="map-plot-card map-quality-card">
          <header><strong>IDOSLAM pipeline</strong><span>{slam?.pairDebugCount ?? 0} frame pairs</span></header>
          <div>
            <p>Pairwise motion records <strong>{compactNumber(slam?.pairwiseMotion.length ?? 0)}</strong></p>
            <p>Plane width estimates <strong>{compactNumber(slam?.planeWidthEstimates.length ?? 0)}</strong></p>
            <p>Triangulated estimates <strong>{compactNumber(slam?.triangulatedWidthEstimates.length ?? 0)}</strong></p>
            <p>Correspondence inlier rate <strong>{slam?.correspondenceCount ? `${((slam.inlierCount / slam.correspondenceCount) * 100).toFixed(1)}%` : 'N/A'}</strong></p>
          </div>
        </section>
      </div>
    </div>
  )
}
