import { useRef, useEffect, useMemo } from 'react'
import {
  Chart,
  LinearScale,
  LineController,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
} from 'chart.js'
Chart.register(LinearScale, LineController, PointElement, LineElement, Tooltip, Legend)
import { useDashboard } from '../context/DashboardContext'
import type { ImuData } from '../types'

const COLORS = ['#ff4466', '#00ff88', '#00d4ff', '#ffaa00']
const HALF_WIN = 300  // frames visible on each side of current position

interface MotionChartProps {
  title: string
  yAxisLabel: string
  field: keyof ImuData
  axisLabels: string[]
  yMin?: number
  yMax?: number
}

const MotionChart = ({ title, yAxisLabel, field, axisLabels, yMin, yMax }: MotionChartProps) => {
  const { sensorData, currentIndex, isLive, displayedFrame } = useDashboard()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)
  const currentIndexRef = useRef(currentIndex)

  // Keep ref in sync so the inline plugin can read it without stale closure
  useEffect(() => {
    currentIndexRef.current = currentIndex
  }, [currentIndex])

  // Inline Chart.js plugin that draws a vertical dashed line at currentIndex
  const currentLinePlugin = useMemo(() => ({
    id: 'currentLine',
    afterDraw(chart: Chart) {
      const xScale = chart.scales['x']
      const yScale = chart.scales['y']
      if (!xScale || !yScale) return
      const x = xScale.getPixelForValue(currentIndexRef.current)
      if (x < xScale.left || x > xScale.right) return
      const { ctx } = chart
      ctx.save()
      ctx.strokeStyle = 'rgba(255,255,255,0.5)'
      ctx.lineWidth = 1
      ctx.setLineDash([4, 4])
      ctx.beginPath()
      ctx.moveTo(x, yScale.top)
      ctx.lineTo(x, yScale.bottom)
      ctx.stroke()
      ctx.restore()
    },
  }), [])  // stable — reads from ref, no deps

  // Build datasets from precomputed sensorData (file mode)
  const fileDatasets = useMemo(() => {
    if (!sensorData.length) return null
    return axisLabels.map((label, i) => ({
      label,
      data: sensorData.map((_f, idx) => {
        const imu = sensorData[idx].imu
        const vec = imu?.[field as keyof typeof imu] as Record<string, number> | undefined
        return { x: idx, y: vec?.[label.toLowerCase()] ?? 0 }
      }),
      borderColor: COLORS[i % COLORS.length],
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0,
    }))
  }, [sensorData, field, axisLabels])

  // Create chart once
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const chart = new Chart(ctx, {
      type: 'line',
      plugins: [currentLinePlugin],
      data: {
        datasets: axisLabels.map((label, i) => ({
          label,
          data: [],
          borderColor: COLORS[i % COLORS.length],
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0,
        })),
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: true,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          x: { type: 'linear', display: false },
          y: {
            title: { display: true, text: yAxisLabel, font: { size: 10 } },
            min: yMin,
            max: yMax,
            ticks: { font: { size: 9 } },
          },
        },
        plugins: {
          legend: { position: 'top', labels: { boxWidth: 12, font: { size: 10 } } },
        },
      },
    })

    chartRef.current = chart
    return () => {
      chart.destroy()
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Update chart datasets when precomputed data arrives (file mode)
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !fileDatasets) return

    for (let i = 0; i < fileDatasets.length; i++) {
      if (chart.data.datasets[i]) {
        chart.data.datasets[i].data = fileDatasets[i].data
      }
    }
    chart.update('none')
  }, [fileDatasets])

  // Pan the x-axis window to keep currentIndex centered (file mode)
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || isLive || !sensorData.length) return

    const xScale = chart.options.scales?.['x']
    if (xScale) {
      xScale.min = Math.max(0, currentIndex - HALF_WIN)
      xScale.max = currentIndex + HALF_WIN
    }
    const yScale = chart.options.scales?.['y']
    if (yScale) {
      if (yMin !== undefined) yScale.min = yMin
      if (yMax !== undefined) yScale.max = yMax
    }
    chart.update('none')
  }, [currentIndex, isLive, sensorData.length, yMin, yMax])

  // Live mode: add the current displayed frame's sensor value
  const addedFrameRef = useRef<number>(-1)
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !isLive) return
    const data = displayedFrame?.imu?.[field]
    if (!data) return
    const fn = displayedFrame?.frame_number ?? 0
    if (fn === addedFrameRef.current) return
    addedFrameRef.current = fn

    const record = data as unknown as Record<string, number>
    const x = Date.now() / 1000

    for (let i = 0; i < axisLabels.length; i++) {
      const ds = chart.data.datasets[i]
      if (!ds) continue
      const arr = ds.data as { x: number; y: number }[]
      arr.push({ x, y: record[axisLabels[i].toLowerCase()] ?? 0 })
      // Rolling 5-minute window
      const cutoff = x - 300
      while (arr.length > 0 && (arr[0] as { x: number }).x < cutoff) arr.shift()
    }

    const xScale = chart.options.scales?.['x']
    if (xScale) {
      xScale.min = x - 300
      xScale.max = x + 10
    }
    chart.update('none')
  }, [displayedFrame, isLive, field, axisLabels])

  // When switching modes, clear live data and reload file data (or vice versa)
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (isLive) {
      // Clear precomputed data; live mode builds incrementally
      for (const ds of chart.data.datasets) {
        ;(ds.data as unknown[]).length = 0
      }
      const xScale = chart.options.scales?.['x']
      if (xScale) { xScale.type = 'linear'; xScale.min = undefined; xScale.max = undefined }
      chart.update('none')
    }
    // File → live transition handled by fileDatasets effect above
  }, [isLive])

  return (
    <div className="stream-card">
      <div className="stream-header">
        <span className="stream-title">{title}</span>
      </div>
      <div style={{ padding: '0.5rem' }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}

export default MotionChart
