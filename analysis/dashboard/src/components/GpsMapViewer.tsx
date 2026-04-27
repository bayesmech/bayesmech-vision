import React, { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { GpsLocation } from '../types'

interface GpsMapViewerProps {
  gps: GpsLocation | undefined
  title?: string
  pathPoints?: GpsLocation[]
}

const GpsMapViewer: React.FC<GpsMapViewerProps> = ({
  gps,
  title = 'GPS Location',
  pathPoints,
}) => {
  const mapContainerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const markerRef = useRef<L.CircleMarker | null>(null)
  const pathRef = useRef<L.Polyline | null>(null)
  const coordsRef = useRef<[number, number][]>([])
  const lastPointKeyRef = useRef<string | null>(null)

  // Initialize map
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return

    const map = L.map(mapContainerRef.current, {
      center: [0, 0],
      zoom: 2,
      attributionControl: false,
    })

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
    }).addTo(map)

    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
      markerRef.current = null
      pathRef.current = null
      coordsRef.current = []
    }
  }, [])

  // File mode: paint the full recorded GPS route once the data is available.
  useEffect(() => {
    if (!mapRef.current) return

    if (!pathPoints || pathPoints.length === 0) {
      coordsRef.current = []
      lastPointKeyRef.current = null
      if (pathRef.current) pathRef.current.setLatLngs([])
      return
    }

    const latlngs = pathPoints.map((point) => [point.latitude, point.longitude] as [number, number])
    coordsRef.current = latlngs

    if (pathRef.current) {
      pathRef.current.setLatLngs(latlngs)
    } else {
      pathRef.current = L.polyline(latlngs, {
        color: '#3498db',
        weight: 3,
        opacity: 0.7,
      }).addTo(mapRef.current)
    }

    if (latlngs.length === 1) {
      mapRef.current.setView(latlngs[0], 17)
    } else {
      mapRef.current.fitBounds(latlngs, { padding: [24, 24], maxZoom: 17 })
    }
  }, [pathPoints])

  // Update marker and path when GPS changes.
  useEffect(() => {
    if (!mapRef.current || !gps) return

    const latlng: [number, number] = [gps.latitude, gps.longitude]
    const pointKey = `${gps.timestamp_ms}:${latlng[0].toFixed(6)}:${latlng[1].toFixed(6)}`

    if (!pathPoints || pathPoints.length === 0) {
      if (pointKey !== lastPointKeyRef.current) {
        coordsRef.current.push(latlng)
        lastPointKeyRef.current = pointKey
      }

      if (pathRef.current) {
        pathRef.current.setLatLngs(coordsRef.current)
      } else {
        pathRef.current = L.polyline(coordsRef.current, {
          color: '#3498db',
          weight: 3,
          opacity: 0.7,
        }).addTo(mapRef.current)
      }
    }

    if (markerRef.current) {
      markerRef.current.setLatLng(latlng)
    } else {
      markerRef.current = L.circleMarker(latlng, {
        radius: 6,
        color: '#e74c3c',
        fillColor: '#e74c3c',
        fillOpacity: 1,
        weight: 2,
      }).addTo(mapRef.current)
    }

    if ((pathPoints?.length ?? 0) <= 1 && coordsRef.current.length <= 1) {
      mapRef.current.setView(latlng, 17)
      return
    }

    mapRef.current.panTo(latlng, { animate: true, duration: 0.3 })
  }, [gps, pathPoints])

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border)',
      overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0.5rem 0.75rem',
        borderBottom: '1px solid var(--border)',
      }}>
        <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>{title}</span>
        {gps && (
          <span style={{ fontSize: '0.7rem', opacity: 0.6 }}>
            {gps.latitude.toFixed(6)}, {gps.longitude.toFixed(6)}
            {gps.accuracy > 0 && ` (${gps.accuracy.toFixed(1)}m)`}
          </span>
        )}
      </div>
      <div
        ref={mapContainerRef}
        style={{
          width: '100%',
          height: '300px',
          background: '#1a1a2e',
        }}
      />
      {gps && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: '0.5rem',
          padding: '0.5rem 0.75rem',
          fontSize: '0.75rem',
          opacity: 0.7,
          borderTop: '1px solid var(--border)',
        }}>
          <span>Alt: {gps.altitude.toFixed(1)}m</span>
          <span>Speed: {gps.speed.toFixed(1)} m/s</span>
          <span>Bearing: {gps.bearing.toFixed(0)}&deg;</span>
        </div>
      )}
      {!gps && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '300px',
          fontSize: '0.85rem',
          opacity: 0.4,
        }}>
          Waiting for GPS fix...
        </div>
      )}
    </div>
  )
}

export default GpsMapViewer
