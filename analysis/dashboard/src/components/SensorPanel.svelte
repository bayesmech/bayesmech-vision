<script lang="ts">
  import { afterUpdate, onMount } from 'svelte';
  import L from 'leaflet';
  import type { DecodedFrameAsset, SensorDataset, SensorFrame } from '$lib/types';
  import DataUnavailable from './DataUnavailable.svelte';
  import Panel from './Panel.svelte';

  export let state: 'idle' | 'loading' | 'ready' | 'empty' | 'error' = 'idle';
  export let dataset: SensorDataset | undefined;
  export let frame: DecodedFrameAsset | undefined;
  export let error = '';

  let accelCanvas: HTMLCanvasElement;
  let gyroCanvas: HTMLCanvasElement;
  let gravCanvas: HTMLCanvasElement;
  let magCanvas: HTMLCanvasElement;
  let pathCanvas: HTMLCanvasElement;
  let pointCanvas: HTMLCanvasElement;
  let planeCanvas: HTMLCanvasElement;
  let mapHost: HTMLDivElement;
  let map: L.Map | undefined;
  let route: L.Polyline | undefined;
  let marker: L.CircleMarker | undefined;

  $: frames = dataset?.frames ?? [];
  $: gpsFrames = frames.filter((row) => row.gps);
  $: currentSensor = nearestSensor(frames, frame?.frameNumber ?? 0);

  onMount(() => {
    drawAll();
    setupMap();
  });

  afterUpdate(() => {
    drawAll();
    setupMap();
  });

  function drawAll() {
    drawChart(accelCanvas, frames, 'linear_acceleration', frame?.frameNumber ?? 0);
    drawChart(gyroCanvas, frames, 'angular_velocity', frame?.frameNumber ?? 0);
    drawChart(gravCanvas, frames, 'gravity', frame?.frameNumber ?? 0);
    drawChart(magCanvas, frames, 'magnetic_field', frame?.frameNumber ?? 0);
    drawPath(pathCanvas, dataset?.trajectory ?? [], frame?.frameIndex ?? 0);
    drawPointCloud(pointCanvas, frame);
    drawPlanes(planeCanvas, frame);
  }

  function setupMap() {
    if (!mapHost || !gpsFrames.length) return;
    if (!map) {
      map = L.map(mapHost, { zoomControl: false, attributionControl: false });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
    }
    const latLngs = gpsFrames.map((row) => [row.gps!.latitude, row.gps!.longitude] as [number, number]);
    if (!route) route = L.polyline(latLngs, { color: '#3498db', weight: 3, opacity: 0.7 }).addTo(map);
    else route.setLatLngs(latLngs);
    const current = currentSensor?.gps ?? gpsFrames.at(-1)?.gps;
    if (current) {
      const pos: [number, number] = [current.latitude, current.longitude];
      if (!marker) marker = L.circleMarker(pos, { radius: 6, color: '#e74c3c', fillColor: '#e74c3c', fillOpacity: 1, weight: 2 }).addTo(map);
      else marker.setLatLng(pos);
    }
    if (latLngs.length) map.fitBounds(latLngs, { padding: [18, 18], maxZoom: 17 });
    setTimeout(() => map?.invalidateSize(), 0);
  }

  const axes = ['#ff4466', '#00ff88', '#00d4ff', '#ffaa00'];

  function drawChart(canvas: HTMLCanvasElement | undefined, frames: SensorFrame[], key: keyof NonNullable<SensorFrame['imu']>, frameNumber: number) {
    if (!canvas) return;
    const width = 800;
    const height = 220;
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.fillStyle = '#030303';
    ctx.fillRect(0, 0, width, height);
    const windowFrames = frames.filter((row) => row.fn >= frameNumber - 300 && row.fn <= frameNumber + 300);
    const rows = windowFrames.length ? windowFrames : frames.slice(-600);
    const channels = ['x', 'y', 'z'] as const;
    const values = rows.flatMap((row) => channels.map((axis) => Number(row.imu?.[key]?.[axis] ?? 0)));
    const min = Math.min(-1, ...values);
    const max = Math.max(1, ...values);
    ctx.strokeStyle = '#1a1a1a';
    for (let y = 30; y < height; y += 45) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    channels.forEach((axis, axisIndex) => {
      ctx.strokeStyle = axes[axisIndex];
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      rows.forEach((row, index) => {
        const x = (index / Math.max(1, rows.length - 1)) * width;
        const v = Number(row.imu?.[key]?.[axis] ?? 0);
        const y = height - ((v - min) / Math.max(0.0001, max - min)) * height;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
    const currentIndex = rows.findIndex((row) => row.fn >= frameNumber);
    if (currentIndex >= 0) {
      const x = (currentIndex / Math.max(1, rows.length - 1)) * width;
      ctx.strokeStyle = 'rgba(255,255,255,0.5)';
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function drawPath(canvas: HTMLCanvasElement | undefined, points: Array<{ x: number; y: number }>, index: number) {
    if (!canvas) return;
    canvas.width = 800;
    canvas.height = 600;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, 800, 600);
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    for (let x = 0; x <= 800; x += 50) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, 600);
      ctx.stroke();
    }
    for (let y = 0; y <= 600; y += 50) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(800, y);
      ctx.stroke();
    }
    if (!points.length) return;
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const map = (p: { x: number; y: number }) => ({
      x: 40 + ((p.x - minX) / Math.max(0.001, maxX - minX)) * 720,
      y: 560 - ((p.y - minY) / Math.max(0.001, maxY - minY)) * 520
    });
    ctx.strokeStyle = 'rgba(0,200,255,0.85)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((point, i) => {
      const p = map(point);
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
    const current = map(points[Math.min(points.length - 1, index)]);
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(current.x, current.y, 6, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawPointCloud(canvas: HTMLCanvasElement | undefined, frame: any) {
    if (!canvas) return;
    canvas.width = 640;
    canvas.height = 360;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, 640, 360);
    ctx.fillStyle = 'rgb(31,188,210)';
    for (const point of frame?.geometry?.pointCloud ?? []) {
      const x = 320 + point.x * 80;
      const y = 180 - point.z * 80;
      if (x >= 0 && x <= 640 && y >= 0 && y <= 360) ctx.fillRect(x, y, 1.4, 1.4);
    }
  }

  function drawPlanes(canvas: HTMLCanvasElement | undefined, frame: any) {
    if (!canvas) return;
    canvas.width = 640;
    canvas.height = 360;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, 640, 360);
    for (const plane of frame?.geometry?.planes ?? []) {
      const color = plane.type === 1 ? ['rgba(64,156,255,0.25)', 'rgba(64,156,255,0.8)'] : plane.type === 2 ? ['rgba(255,200,64,0.25)', 'rgba(255,200,64,0.8)'] : ['rgba(100,220,100,0.25)', 'rgba(100,220,100,0.8)'];
      ctx.fillStyle = color[0];
      ctx.strokeStyle = color[1];
      ctx.beginPath();
      const poly = plane.polygon?.length ? plane.polygon : [{ x: -plane.extentX / 2, z: -plane.extentZ / 2 }, { x: plane.extentX / 2, z: -plane.extentZ / 2 }, { x: plane.extentX / 2, z: plane.extentZ / 2 }, { x: -plane.extentX / 2, z: plane.extentZ / 2 }];
      poly.forEach((point: any, index: number) => {
        const x = 320 + point.x * 80;
        const y = 180 + point.z * 80;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    }
  }

  function coverageRows(frames: SensorFrame[], frame: any) {
    const total = Math.max(1, frames.length);
    const imu = (frames.filter((row) => row.imu).length / total) * 100;
    const gps = (frames.filter((row) => row.gps).length / total) * 100;
    return [
      { label: 'IMU', value: imu, color: coverageColor(imu) },
      { label: 'GPS', value: gps, color: coverageColor(gps) },
      { label: 'Depth', value: frame?.metadata?.hasDepth ? 100 : 0, color: frame?.metadata?.hasDepth ? '#00ff88' : '#c0392b' },
      { label: 'Geometry', value: frame?.metadata?.hasGeometry ? 100 : 0, color: frame?.metadata?.hasGeometry ? '#00ff88' : '#c0392b' }
    ];
  }

  function coverageColor(value: number) {
    if (value >= 80) return '#00ff88';
    if (value >= 40) return '#e6a817';
    return '#c0392b';
  }

  function nearestSensor(frames: SensorFrame[], frameNumber: number) {
    return frames.reduce<SensorFrame | undefined>((best, row) => (!best || Math.abs(row.fn - frameNumber) < Math.abs(best.fn - frameNumber) ? row : best), undefined);
  }

  function fmt(value: number | undefined) {
    return Number.isFinite(value) ? Number(value).toFixed(1) : '--';
  }
</script>

{#if state === 'error'}
  <DataUnavailable message="Sensor load failed" detail={error} />
{:else if state === 'loading' || state === 'idle'}
  <DataUnavailable message={state === 'loading' ? 'Loading full sensor timeline' : 'Sensor data idle'} />
{:else if state === 'empty'}
  <DataUnavailable message="No sensor data available" />
{:else}
  <Panel title="Signal Coverage">
    <div class="coverage">
      {#each coverageRows(frames, frame) as row}
        <div>
          <span>{row.label}</span>
          <i><b style={`width:${row.value}%; background:${row.color}`}></b></i>
          <strong>{row.value.toFixed(0)}%</strong>
        </div>
      {/each}
    </div>
  </Panel>

  <div class="chart-grid">
    <Panel title="Accelerometer"><canvas bind:this={accelCanvas}></canvas></Panel>
    <Panel title="Gyroscope"><canvas bind:this={gyroCanvas}></canvas></Panel>
    <Panel title="Gravitometer"><canvas bind:this={gravCanvas}></canvas></Panel>
    <Panel title="Magnetometer"><canvas bind:this={magCanvas}></canvas></Panel>
  </div>

  <div class="streams-grid">
    <Panel title="Point Cloud"><canvas class="geom" bind:this={pointCanvas}></canvas></Panel>
    <Panel title="Plane Detection"><canvas class="geom" bind:this={planeCanvas}></canvas></Panel>
  </div>

  <div class="path-grid">
    <Panel title="SLAM Path">
      <canvas class="path" bind:this={pathCanvas}></canvas>
      <footer>Points {dataset?.trajectory.length ?? 0} - Frame {frame?.frameIndex ?? 0}</footer>
    </Panel>
    <Panel title="GPS Route">
      {#if gpsFrames.length}
        <div class="map" bind:this={mapHost}></div>
        <footer>
          <span>Alt {fmt(currentSensor?.gps?.altitude)} m</span>
          <span>Speed {fmt(currentSensor?.gps?.speed)} m/s</span>
          <span>Bearing {fmt(currentSensor?.gps?.bearing)} deg</span>
        </footer>
      {:else}
        <DataUnavailable message="No GPS samples" />
      {/if}
    </Panel>
  </div>
{/if}

<style>
  .coverage {
    display: flex;
    flex-direction: column;
  }

  .coverage div {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.35rem 0;
  }

  .coverage span {
    width: 140px;
    color: rgba(224, 224, 224, 0.7);
    font-size: 0.8rem;
  }

  .coverage i {
    flex: 1;
    height: 4px;
    background: rgba(255, 255, 255, 0.06);
  }

  .coverage b {
    display: block;
    height: 100%;
    transition: width 0.4s, background 0.4s;
  }

  .coverage strong {
    width: 80px;
    text-align: right;
    font-size: 0.85rem;
  }

  .chart-grid,
  .streams-grid,
  .path-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
    margin-top: 1rem;
  }

  canvas {
    width: 100%;
    height: auto;
    background: #030303;
    border: 1px solid var(--border);
    display: block;
  }

  .geom {
    aspect-ratio: 16 / 9;
  }

  .path {
    aspect-ratio: 4 / 3;
  }

  .map {
    width: 100%;
    height: 300px;
    background: #1a1a2e;
  }

  footer {
    display: flex;
    gap: 1.5rem;
    margin-top: 0.5rem;
    color: rgba(224, 224, 224, 0.7);
    font-size: 0.75rem;
  }

  @media (max-width: 720px) {
    .chart-grid,
    .streams-grid,
    .path-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
