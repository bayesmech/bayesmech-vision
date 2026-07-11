<script lang="ts">
  import { afterUpdate, onDestroy, onMount } from 'svelte';
  import * as THREE from 'three';
  import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
  import type { Pong3DState } from '$lib/types';

  export let state3d: Pong3DState | undefined;
  export let frameIndex = 0;
  export let poseCorrections = true;

  let host: HTMLDivElement;
  let renderer: THREE.WebGLRenderer;
  let scene: THREE.Scene;
  let camera: THREE.PerspectiveCamera;
  let controls: OrbitControls;
  let tableGroup: THREE.Group;
  let markerGroup: THREE.Group;
  let raf = 0;
  let resizeObserver: ResizeObserver;
  let tableSignature = '';

  onMount(() => {
    scene = new THREE.Scene();
    scene.background = new THREE.Color('#050706');
    camera = new THREE.PerspectiveCamera(44, 1, 0.05, 80);
    camera.position.set(0, 4.2, 5.4);
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.shadowMap.enabled = true;
    host.appendChild(renderer.domElement);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.minDistance = 1.5;
    controls.maxDistance = 9;
    controls.maxPolarAngle = Math.PI * 0.48;
    scene.add(new THREE.AmbientLight(0xffffff, 0.58));
    const key = new THREE.DirectionalLight(0xffffff, 1.15);
    key.position.set(3, 6, 4);
    key.castShadow = true;
    scene.add(key);
    const fill = new THREE.DirectionalLight('#7fffd2', 0.24);
    fill.position.set(-4, 3, -3);
    scene.add(fill);
    tableGroup = new THREE.Group();
    markerGroup = new THREE.Group();
    scene.add(tableGroup, markerGroup);
    resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(host);
    maybeRebuildTable();
    updateMarkers();
    renderLoop();
  });

  afterUpdate(() => {
    maybeRebuildTable();
    updateMarkers();
  });

  onDestroy(() => {
    cancelAnimationFrame(raf);
    resizeObserver?.disconnect();
    controls?.dispose();
    renderer?.dispose();
  });

  function renderLoop() {
    raf = requestAnimationFrame(renderLoop);
    controls?.update();
    renderer?.render(scene, camera);
  }

  function resize() {
    if (!renderer || !host) return;
    const width = host.clientWidth;
    const height = host.clientHeight;
    renderer.setSize(width, height, false);
    camera.aspect = width / Math.max(1, height);
    camera.updateProjectionMatrix();
  }

  function maybeRebuildTable() {
    const signature = state3d ? `${state3d.sportMode}:${state3d.tableWidthMm}:${state3d.tableHeightMm}:${state3d.netHeightMm}` : '';
    if (signature === tableSignature) return;
    tableSignature = signature;
    rebuildTable();
  }

  function rebuildTable() {
    if (!tableGroup || !state3d) return;
    tableGroup.clear();
    const w = state3d.tableWidthMm / 1000;
    const h = state3d.tableHeightMm / 1000;
    const surfaceColor = state3d.sportMode === 'snooker' ? '#0f5638' : '#145c40';
    const surface = new THREE.Mesh(
      new THREE.BoxGeometry(w, 0.06, h),
      new THREE.MeshStandardMaterial({ color: surfaceColor, roughness: 0.82 })
    );
    surface.receiveShadow = true;
    tableGroup.add(surface);
    const edgeMat = new THREE.MeshStandardMaterial({ color: state3d.sportMode === 'snooker' ? '#0a3b29' : '#f8fbf4', roughness: 0.6 });
    const railW = 0.06;
    for (const [x, z, sx, sz] of [
      [0, -h / 2, w + railW * 2, railW],
      [0, h / 2, w + railW * 2, railW],
      [-w / 2, 0, railW, h],
      [w / 2, 0, railW, h]
    ]) {
      const rail = new THREE.Mesh(new THREE.BoxGeometry(sx, 0.12, sz), edgeMat);
      rail.position.set(x, 0.06, z);
      tableGroup.add(rail);
    }
    if (state3d.sportMode !== 'snooker') {
      const net = new THREE.Mesh(
        new THREE.BoxGeometry(0.035, state3d.netHeightMm / 1000, h + 0.22),
        new THREE.MeshStandardMaterial({ color: '#2c2f35', roughness: 0.65 })
      );
      net.position.set(0, 0.13, 0);
      tableGroup.add(net);
    }
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(Math.max(7, w + 2), Math.max(5, h + 2)),
      new THREE.MeshStandardMaterial({ color: '#111611', roughness: 0.9 })
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -0.08;
    floor.receiveShadow = true;
    tableGroup.add(floor);
  }

  function updateMarkers() {
    if (!markerGroup || !state3d) return;
    markerGroup.clear();
    const recentBounce = state3d.bounces.filter((bounce) => bounce.frameIdx <= frameIndex).at(-1);
    for (const bounce of state3d.bounces) {
      if (bounce.frameIdx > frameIndex) continue;
      const active = recentBounce === bounce;
      const material = new THREE.MeshStandardMaterial({
        color: active ? '#ff5d35' : bounce.corrected ? '#5fd1ff' : bounce.insideTable ? '#ffd84d' : '#a7acb2',
        emissive: active ? '#431300' : '#000000',
        emissiveIntensity: active ? (poseCorrections ? 0.8 : 0.35) : 0.15,
        opacity: poseCorrections || !bounce.corrected ? 1 : 0.65,
        transparent: !poseCorrections && Boolean(bounce.corrected)
      });
      const marker = new THREE.Mesh(new THREE.SphereGeometry(active ? 0.055 : 0.035, 24, 16), material);
      marker.position.set((bounce.xMm || 0) / 1000, 0.15 + (bounce.zMm || 0) / 1000, (bounce.yMm || 0) / 1000);
      marker.castShadow = true;
      markerGroup.add(marker);
    }
    for (const ball of state3d.balls.filter((ball) => Math.abs(ball.frameIdx - frameIndex) < 3).slice(0, 24)) {
      const marker = new THREE.Mesh(new THREE.SphereGeometry(0.045, 20, 14), new THREE.MeshStandardMaterial({ color: ball.color, roughness: 0.35 }));
      marker.position.set((ball.xMm || 0) / 1000, 0.13 + (ball.zMm || 0) / 1000, (ball.yMm || 0) / 1000);
      markerGroup.add(marker);
    }
  }
</script>

<div class="three-host" bind:this={host} aria-label="3D trajectory understanding"></div>

<style>
  .three-host {
    width: 100%;
    height: clamp(360px, 48vh, 640px);
    background: #050706;
    border: 1px solid var(--border);
    cursor: grab;
  }

  .three-host:active {
    cursor: grabbing;
  }

  .three-host :global(canvas) {
    width: 100%;
    height: 100%;
    display: block;
  }
</style>
