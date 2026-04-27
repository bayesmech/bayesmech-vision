import React, { useState } from 'react'
import { useDashboard } from '../context/DashboardContext'
import StreamViewer from './StreamViewer'
import GeometryStreamViewer from './GeometryStreamViewer'
import PlaybackControls from './PlaybackControls'
import MotionChart from './MotionChart'
import InfoCard from './InfoCard'
import TrajectoryCanvas from './TrajectoryCanvas'
import GpsMapViewer from './GpsMapViewer'
import LocalizationMappingTab from './LocalizationMappingTab'
import type { ImuData, SegmentationLegendEntry } from '../types'

const XYZ = ['X', 'Y', 'Z']

const SENSOR_CHARTS: {
  field: keyof ImuData
  title: string
  yAxisLabel: string
  axisLabels: string[]
}[] = [
  { field: 'linear_acceleration', title: 'Linear Acceleration', yAxisLabel: 'm/s²', axisLabels: XYZ },
  { field: 'angular_velocity', title: 'Angular Velocity', yAxisLabel: 'rad/s', axisLabels: XYZ },
  { field: 'gravity', title: 'Gravity', yAxisLabel: 'm/s²', axisLabels: XYZ },
  { field: 'magnetic_field', title: 'Magnetic Field', yAxisLabel: 'µT', axisLabels: XYZ },
]

const SegmentationLegend: React.FC<{ legend?: SegmentationLegendEntry[] }> = ({ legend }) => (
  <div className="stream-card" style={{ display: 'flex', flexDirection: 'column' }}>
    <div className="stream-header">
      <span className="stream-title">Legend</span>
      <span className="stream-badge">KEY</span>
    </div>
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', overflowY: 'auto' }}>
      {(!legend || legend.length === 0) ? (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>No objects detected</div>
      ) : (
        legend.map(entry => (
          <div key={entry.objectId} style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem' }}>
            <div style={{
              width: 12,
              height: 12,
              flexShrink: 0,
              marginTop: 2,
              background: `rgba(${entry.color[0]}, ${entry.color[1]}, ${entry.color[2]}, 0.7)`,
              border: `1px solid rgba(${entry.color[0]}, ${entry.color[1]}, ${entry.color[2]}, 1)`,
            }} />
            <span style={{
              fontSize: '0.78rem',
              lineHeight: 1.35,
              color: 'var(--text)',
              wordBreak: 'break-word',
              overflowWrap: 'anywhere',
            }}>
              {entry.label}
            </span>
          </div>
        ))
      )}
    </div>
  </div>
)

const DashboardPage = () => {
  const { displayedFrame, displayedAnnotation, frameCount, fps } = useDashboard()
  const [activeTab, setActiveTab] = useState<'capture' | 'slam'>('capture')

  const source = displayedFrame?.source ?? 'none'
  const deviceId = displayedFrame?.device_id
    ? displayedFrame.device_id.slice(0, 8)
    : 'N/A'

  return (
    <section className="stream-section">
      {/* Playback controls — full width, above all streams */}
      <PlaybackControls />

      <div className="dashboard-tabs">
        <button
          className={activeTab === 'capture' ? 'active' : ''}
          onClick={() => setActiveTab('capture')}
          type="button"
        >
          Capture
        </button>
        <button
          className={activeTab === 'slam' ? 'active' : ''}
          onClick={() => setActiveTab('slam')}
          type="button"
        >
          Localization + Mapping
        </button>
      </div>

      {activeTab === 'slam' ? (
        <LocalizationMappingTab />
      ) : (
        <>
      {/* Video streams — RGB, Depth, Point Cloud, Planes */}
      <div
        className="streams-grid"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(400px, 1fr))',
          gap: '1rem',
          marginBottom: '1.5rem',
        }}
      >
        <StreamViewer
          title="RGB Stream"
          badge="RGB"
          blobUrl={displayedFrame?.rgbBlobUrl}
          placeholderIcon={'🎥'}
          placeholderText="Waiting for RGB frames..."
        />

        <StreamViewer
          title="Depth Map"
          badge="DEPTH"
          blobUrl={displayedFrame?.depthBlobUrl}
          placeholderIcon={'🌊'}
          placeholderText="Waiting for depth frames..."
        />

        <GeometryStreamViewer
          title="Point Cloud"
          badge="PCD"
          placeholderIcon={'✦'}
          placeholderText="Waiting for point cloud data..."
          mode="point_cloud"
          cameraPose={displayedFrame?.camera_pose}
          cameraIntrinsics={displayedFrame?.camera_intrinsics}
          geometry={displayedFrame?.inferred_geometry}
        />

        <GeometryStreamViewer
          title="Plane Detection"
          badge="PLANE"
          placeholderIcon={'⬛'}
          placeholderText="Waiting for plane data..."
          mode="planes"
          cameraPose={displayedFrame?.camera_pose}
          cameraIntrinsics={displayedFrame?.camera_intrinsics}
          geometry={displayedFrame?.inferred_geometry}
        />
      </div>

      {/* Segmentation row — video (1 col) + legend (0.5 col) out of 3-column grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '2fr 1fr 3fr',
          gap: '1rem',
          marginBottom: '1.5rem',
        }}
      >
        <StreamViewer
          title="Segmentation"
          badge="SEG"
          blobUrl={displayedAnnotation?.blobUrl}
          placeholderIcon={'🧩'}
          placeholderText="Waiting for segmentation masks..."
          holdLastMs={3000}
        />
        <SegmentationLegend legend={displayedAnnotation?.legend} />
      </div>

      {/* Info cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
          gap: '1rem',
          marginBottom: '1.5rem',
        }}
      >
        <InfoCard value={frameCount} label="Frames Received" />
        <InfoCard value={fps.toFixed(1)} label="FPS" />
        <InfoCard value={source} label="Source" />
        <InfoCard value={deviceId} label="Device ID" />
        <InfoCard
          value={displayedFrame?.inferred_geometry?.plane_count ?? 0}
          label="Planes Detected"
        />
        <InfoCard
          value={displayedFrame?.inferred_geometry?.point_cloud_count ?? 0}
          label="Point Cloud Pts"
        />
        <InfoCard
          value={
            displayedFrame?.gps
              ? `${displayedFrame.gps.latitude.toFixed(4)}, ${displayedFrame.gps.longitude.toFixed(4)}`
              : 'N/A'
          }
          label="GPS Position"
        />
      </div>

      {/* Sensor charts */}
      <h3 className="section-title" style={{ marginBottom: '1rem' }}>Sensor Data</h3>
      <div
        className="sensor-charts-grid"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(400px, 1fr))',
          gap: '1rem',
          marginBottom: '1.5rem',
        }}
      >
        {SENSOR_CHARTS.map((chart) => (
          <MotionChart key={chart.field} {...chart} />
        ))}
      </div>

      {/* Trajectory & GPS map */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(400px, 1fr))',
          gap: '1rem',
        }}
      >
        <TrajectoryCanvas />
        <GpsMapViewer gps={displayedFrame?.gps} />
      </div>
        </>
      )}
    </section>
  )
}

export default DashboardPage
