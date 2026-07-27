import { Activity, Gauge, LoaderCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { SensorDataSummary, SensorSample, Vec3 } from '../types'
import SensorOrientationScene from './SensorOrientationScene'

type SensorDataPanelProps = {
  currentFrameIndex: number
  sourcePath?: string
  getSensorData: (sourcePath?: string) => Promise<SensorDataSummary | null>
}

type VectorField = 'linearAcceleration' | 'angularVelocity' | 'gravity' | 'magneticField'

const AXES = [
  { key: 'x' as const, label: 'X', color: '#d7687d' },
  { key: 'y' as const, label: 'Y', color: '#62d2a2' },
  { key: 'z' as const, label: 'Z', color: '#5aa9e6' },
]

const SENSOR_CHARTS: Array<{ field: VectorField; title: string; unit: string; description: string }> = [
  { field: 'linearAcceleration', title: 'Accelerometer', unit: 'm/s²', description: 'Linear acceleration' },
  { field: 'angularVelocity', title: 'Gyroscope', unit: 'rad/s', description: 'Angular velocity' },
  { field: 'gravity', title: 'Gravitometer', unit: 'm/s²', description: 'Gravity vector' },
  { field: 'magneticField', title: 'Magnetometer', unit: 'µT', description: 'Ambient magnetic field' },
]

const HALF_WINDOW = 300
const CHART_WIDTH = 720
const CHART_HEIGHT = 214
const CHART_PADDING = { top: 20, right: 18, bottom: 28, left: 48 }

function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

function vectorLabel(value: Vec3 | null, unit: string): string {
  if (!value) return 'No sample'
  return `${value.x.toFixed(2)}, ${value.y.toFixed(2)}, ${value.z.toFixed(2)} ${unit}`
}

function sampleAt(data: SensorDataSummary, frameIndex: number): SensorSample | null {
  const direct = data.samples[frameIndex]
  if (direct?.index === frameIndex) return direct
  let nearest: SensorSample | null = null
  let distance = Number.POSITIVE_INFINITY
  for (const sample of data.samples) {
    const nextDistance = Math.abs(sample.index - frameIndex)
    if (nextDistance < distance) {
      nearest = sample
      distance = nextDistance
    }
  }
  return nearest
}

function downsample<T>(items: T[], maximum: number): T[] {
  if (items.length <= maximum) return items
  const output: T[] = []
  const stride = (items.length - 1) / (maximum - 1)
  for (let index = 0; index < maximum; index += 1) {
    output.push(items[Math.round(index * stride)])
  }
  return output
}

function SensorChart({
  data,
  field,
  title,
  unit,
  description,
  currentFrameIndex,
}: {
  data: SensorDataSummary
  field: VectorField
  title: string
  unit: string
  description: string
  currentFrameIndex: number
}) {
  const current = sampleAt(data, currentFrameIndex)?.[field] ?? null
  const plotWidth = CHART_WIDTH - CHART_PADDING.left - CHART_PADDING.right
  const plotHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom
  // The trace window is a buffered page. Its expensive paths remain unchanged
  // for hundreds of playback ticks; only the lightweight playhead moves.
  const windowPage = Math.floor(currentFrameIndex / HALF_WINDOW)
  const geometry = useMemo(() => {
    const center = windowPage * HALF_WINDOW
    const start = Math.max(0, center - HALF_WINDOW)
    const end = Math.min(data.frameCount - 1, center + HALF_WINDOW)
    const windowSamples = downsample(
      data.samples.filter((sample) => sample.index >= start && sample.index <= end),
      720,
    )
    const vectors = windowSamples.flatMap((sample) => (
      sample[field] ? [sample[field] as Vec3] : []
    ))
    const values = vectors.flatMap((vector) => [vector.x, vector.y, vector.z])
    const rawMin = values.length ? Math.min(...values) : -1
    const rawMax = values.length ? Math.max(...values) : 1
    const spread = Math.max(rawMax - rawMin, 0.001)
    const min = rawMin - spread * 0.08
    const max = rawMax + spread * 0.08
    const firstIndex = windowSamples[0]?.index ?? start
    const lastIndex = windowSamples[windowSamples.length - 1]?.index ?? Math.max(start + 1, end)
    const xSpan = Math.max(1, lastIndex - firstIndex)
    const xFor = (index: number) => (
      CHART_PADDING.left + ((index - firstIndex) / xSpan) * plotWidth
    )
    const yFor = (value: number) => (
      CHART_PADDING.top
      + (1 - (value - min) / Math.max(max - min, 0.001)) * plotHeight
    )
    const axisPoints = Object.fromEntries(AXES.map((axis) => [
      axis.key,
      windowSamples
        .filter((sample) => sample[field])
        .map((sample) => (
          `${xFor(sample.index).toFixed(1)},${yFor((sample[field] as Vec3)[axis.key]).toFixed(1)}`
        ))
        .join(' '),
    ])) as Record<(typeof AXES)[number]['key'], string>
    return { axisPoints, firstIndex, lastIndex, max, min, xFor }
  }, [data, field, plotHeight, plotWidth, windowPage])
  const markerX = geometry.xFor(
    Math.max(geometry.firstIndex, Math.min(geometry.lastIndex, currentFrameIndex)),
  )

  return (
    <section className="sensor-chart-card">
      <header>
        <div>
          <strong>{title}</strong>
          <span>{description}</span>
        </div>
        <output>{vectorLabel(current, unit)}</output>
      </header>
      <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} role="img" aria-label={`${title} readings around frame ${currentFrameIndex + 1}`}>
        <rect className="sensor-chart-background" x="0" y="0" width={CHART_WIDTH} height={CHART_HEIGHT} />
        {[0, 0.25, 0.5, 0.75, 1].map((part) => {
          const y = CHART_PADDING.top + part * plotHeight
          const value = geometry.max - part * (geometry.max - geometry.min)
          return (
            <g key={part}>
              <line className="plot-grid-line" x1={CHART_PADDING.left} x2={CHART_WIDTH - CHART_PADDING.right} y1={y} y2={y} />
              <text className="sensor-chart-axis-label" x={CHART_PADDING.left - 7} y={y + 3} textAnchor="end">{value.toFixed(1)}</text>
            </g>
          )
        })}
        {AXES.map((axis) => {
          return <polyline key={axis.key} points={geometry.axisPoints[axis.key]} fill="none" stroke={axis.color} strokeWidth="1.7" vectorEffect="non-scaling-stroke" />
        })}
        <line className="sensor-chart-current" x1={markerX} x2={markerX} y1={CHART_PADDING.top} y2={CHART_HEIGHT - CHART_PADDING.bottom} />
        <text className="sensor-chart-frame-label" x={markerX} y={CHART_HEIGHT - 8} textAnchor="middle">frame {currentFrameIndex + 1}</text>
      </svg>
      <footer>
        <span>{unit}</span>
        <div className="sensor-axis-legend">
          {AXES.map((axis) => <span key={axis.key} style={{ color: axis.color }}>{axis.label}</span>)}
        </div>
      </footer>
    </section>
  )
}

export default function SensorDataPanel({
  currentFrameIndex,
  sourcePath,
  getSensorData,
}: SensorDataPanelProps) {
  const [data, setData] = useState<SensorDataSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)
    getSensorData(sourcePath)
      .then((next) => {
        if (!cancelled) setData(next)
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Failed to load sensor data.')
      })
    return () => {
      cancelled = true
    }
  }, [getSensorData, sourcePath])

  const availability = useMemo(() => data ? SENSOR_CHARTS.map((chart) => ({
    ...chart,
    count: data.samples.filter((sample) => Boolean(sample[chart.field])).length,
  })) : [], [data])

  if (error && !data) {
    return <div className="sensor-panel-state is-error"><Activity size={22} aria-hidden="true" /><span>{error}</span></div>
  }
  if (!data) {
    return <div className="sensor-panel-state"><LoaderCircle className="sensor-loading-icon" size={22} aria-hidden="true" /><span>Loading recorded sensors…</span></div>
  }

  const current = sampleAt(data, currentFrameIndex)

  return (
    <div className="sensor-data-panel">
      <div className="sensor-panel-heading">
        <div>
          <span className="eyebrow">Recorded streams</span>
          <h2>Sensor Data</h2>
          <p>IMU readings follow the shared video playhead. GPS and IDOSLAM outputs live in Map Generation.</p>
        </div>
        <div className="sensor-frame-chip">
          <Gauge size={15} aria-hidden="true" />
          <span>Frame {currentFrameIndex + 1}</span>
          <strong>{current?.deviceId ? current.deviceId.slice(0, 8) : 'No device'}</strong>
        </div>
      </div>

      <div className="sensor-availability-grid">
        {availability.map((sensor) => (
          <div className="sensor-availability-card" key={sensor.field}>
            <span>{sensor.title}</span>
            <strong>{compactNumber(sensor.count)}</strong>
            <small>{sensor.count === 1 ? 'sample' : 'samples'}</small>
          </div>
        ))}
      </div>

      <SensorOrientationScene sample={current} />

      <div className="sensor-chart-grid">
        {SENSOR_CHARTS.map((chart) => (
          <SensorChart key={chart.field} {...chart} data={data} currentFrameIndex={currentFrameIndex} />
        ))}
      </div>
    </div>
  )
}
