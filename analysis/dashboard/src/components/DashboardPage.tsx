import React, { useMemo, useState } from 'react'
import { useDashboard } from '../context/DashboardContext'
import StreamViewer from './StreamViewer'
import GeometryStreamViewer from './GeometryStreamViewer'
import PlaybackControls from './PlaybackControls'
import MotionChart from './MotionChart'
import InfoCard from './InfoCard'
import TrajectoryCanvas from './TrajectoryCanvas'
import GpsMapViewer from './GpsMapViewer'
import CoveragePanel from './CoveragePanel'
import MotioncapPanel from './MotioncapPanel'
import type { GpsLocation, ImuData, SegmentationLegendEntry, SensorFrameData } from '../types'

const XYZ = ['X', 'Y', 'Z']

type DashboardTabId =
  | 'segmentation'
  | 'motion-capture'
  | 'stable-entity-understanding'
  | 'sensors'
  | 'path-planning'

const DASHBOARD_TABS: {
  id: DashboardTabId
  label: string
  badge: string
  description: string
}[] = [
  {
    id: 'segmentation',
    label: 'Segmentation',
    badge: 'SEG',
    description: 'Overlay masks and object legends for the current frame.',
  },
  {
    id: 'motion-capture',
    label: 'Motion Capture',
    badge: 'MCAP',
    description: 'Review motion heatmaps and tracked object paths without on-frame labels.',
  },
  {
    id: 'stable-entity-understanding',
    label: 'Stable entity understanding',
    badge: 'SEU',
    description: 'Inspect depth, point cloud, and plane detections for the current frame.',
  },
  {
    id: 'sensors',
    label: 'Sensor Data',
    badge: 'IMU',
    description: 'Inspect the live or recorded IMU streams and signal coverage.',
  },
  {
    id: 'path-planning',
    label: 'Path Planning',
    badge: 'NAV',
    description: 'Compare GPS motion with the SLAM-generated trajectory.',
  },
]

const SENSOR_CHARTS: {
  field: keyof ImuData
  title: string
  yAxisLabel: string
  axisLabels: string[]
}[] = [
  { field: 'linear_acceleration', title: 'Accelerometer', yAxisLabel: 'm/s²', axisLabels: XYZ },
  { field: 'angular_velocity', title: 'Gyroscope', yAxisLabel: 'rad/s', axisLabels: XYZ },
  { field: 'gravity', title: 'Gravitometer', yAxisLabel: 'm/s²', axisLabels: XYZ },
  { field: 'magnetic_field', title: 'Magnetometer', yAxisLabel: 'µT', axisLabels: XYZ },
]

const findCurrentGps = (
  frames: SensorFrameData[],
  currentIndex: number,
  fallback?: GpsLocation,
): GpsLocation | undefined => {
  for (let i = Math.min(currentIndex, frames.length - 1); i >= 0; i--) {
    if (frames[i]?.gps) return frames[i].gps
  }
  return fallback
}

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
  const {
    displayedFrame,
    displayedAnnotation,
    frameCount,
    fps,
    currentIndex,
    currentRecordingName,
    totalFrames,
    serverFps,
    isLive,
    sensorData,
    trajectoryPositions,
  } = useDashboard()
  const [activeTab, setActiveTab] = useState<DashboardTabId>('segmentation')

  const source = displayedFrame?.source ?? 'none'
  const deviceId = displayedFrame?.device_id
    ? displayedFrame.device_id.slice(0, 8)
    : 'N/A'

  const gpsTrack = useMemo(
    () => sensorData.flatMap((frame) => (frame.gps ? [frame.gps] : [])),
    [sensorData],
  )

  const currentGps = useMemo(() => {
    if (isLive) return displayedFrame?.gps
    return findCurrentGps(sensorData, currentIndex, displayedFrame?.gps)
  }, [currentIndex, displayedFrame, isLive, sensorData])

  const frameLabel = isLive
    ? `${frameCount}`
    : totalFrames > 0
      ? `${Math.min(currentIndex + 1, totalFrames)} / ${totalFrames}`
      : '0 / 0'
  const playbackRate = isLive ? `${fps.toFixed(1)} fps` : `${serverFps.toFixed(1)} fps`
  const gpsPositionLabel = currentGps
    ? `${currentGps.latitude.toFixed(4)}, ${currentGps.longitude.toFixed(4)}`
    : 'N/A'

  return (
    <section className="stream-section">
      {/* Playback controls — full width, above all streams */}
      <PlaybackControls />

      {/* Primary video stream */}
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
      </div>

      <div className="dashboard-workspace">
        <aside className="dashboard-sidebar">
          <div
            className="dashboard-tabs"
            role="tablist"
            aria-label="Dashboard panel groups"
            aria-orientation="vertical"
          >
            {DASHBOARD_TABS.map((tab) => {
              const isActive = tab.id === activeTab
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  id={`dashboard-tab-${tab.id}`}
                  aria-selected={isActive}
                  aria-controls={`dashboard-panel-${tab.id}`}
                  className={`dashboard-tab${isActive ? ' is-active' : ''}`}
                  onClick={() => setActiveTab(tab.id)}
                >
                  <span className="dashboard-tab-badge">{tab.badge}</span>
                  <span className="dashboard-tab-copy">
                    <span className="dashboard-tab-label">{tab.label}</span>
                    <span className="dashboard-tab-description">{tab.description}</span>
                  </span>
                </button>
              )
            })}
          </div>
        </aside>

        <div
          className="dashboard-panel"
          role="tabpanel"
          id={`dashboard-panel-${activeTab}`}
          aria-labelledby={`dashboard-tab-${activeTab}`}
        >
          {activeTab === 'segmentation' && (
            <>
              <div className="dashboard-summary-grid">
                <InfoCard value={frameLabel} label={isLive ? 'Frames Received' : 'Playback Position'} />
                <InfoCard value={playbackRate} label={isLive ? 'Live Rate' : 'Playback Rate'} />
                <InfoCard value={source} label="Source" />
                <InfoCard value={deviceId} label="Device ID" />
                <InfoCard value={displayedAnnotation?.legend.length ?? 0} label="Objects Tracked" />
              </div>

              <div className="dashboard-segmentation-grid">
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
            </>
          )}

          {activeTab === 'sensors' && (
            <>
              {isLive && (
                <div style={{ marginBottom: '1.25rem' }}>
                  <CoveragePanel />
                </div>
              )}

              <div className="dashboard-chart-grid">
                {SENSOR_CHARTS.map((chart) => (
                  <MotionChart key={chart.field} {...chart} />
                ))}
              </div>
            </>
          )}

          {activeTab === 'motion-capture' && (
            <MotioncapPanel key={currentRecordingName ?? (isLive ? 'live' : 'idle')} />
          )}

          {activeTab === 'stable-entity-understanding' && (
            <>
              <div className="dashboard-summary-grid">
                <InfoCard value={displayedFrame?.hasDepthData ? 'Available' : 'N/A'} label="Depth Map" />
                <InfoCard
                  value={displayedFrame?.inferred_geometry?.point_cloud_count ?? 0}
                  label="Point Cloud Pts"
                />
                <InfoCard
                  value={displayedFrame?.inferred_geometry?.plane_count ?? 0}
                  label="Planes Detected"
                />
              </div>

              <div
                className="streams-grid"
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
                  gap: '1rem',
                }}
              >
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
            </>
          )}

          {activeTab === 'path-planning' && (
            <>
              <div className="dashboard-summary-grid">
                <InfoCard value={gpsPositionLabel} label="GPS Position" />
                <InfoCard
                  value={currentGps ? `${currentGps.accuracy.toFixed(1)} m` : 'N/A'}
                  label="GPS Accuracy"
                />
                <InfoCard
                  value={currentGps ? `${currentGps.speed.toFixed(1)} m/s` : 'N/A'}
                  label="Speed"
                />
                <InfoCard value={trajectoryPositions.length} label="SLAM Points" />
                <InfoCard value={gpsTrack.length} label="GPS Samples" />
                <InfoCard
                  value={displayedFrame?.inferred_geometry?.plane_count ?? 0}
                  label="Planes Detected"
                />
                <InfoCard
                  value={displayedFrame?.inferred_geometry?.point_cloud_count ?? 0}
                  label="Point Cloud Pts"
                />
              </div>

              <div className="dashboard-path-grid">
                <TrajectoryCanvas title="SLAM Path" />
                <GpsMapViewer
                  gps={currentGps}
                  pathPoints={isLive ? undefined : gpsTrack}
                  title="GPS Route"
                />
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

export default DashboardPage
