<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import DashboardTabs from '$components/DashboardTabs.svelte';
  import EndpointHealthDialog from '$components/EndpointHealthDialog.svelte';
  import Header from '$components/Header.svelte';
  import LocalizationPanel from '$components/LocalizationPanel.svelte';
  import MetricGrid from '$components/MetricGrid.svelte';
  import ModelMusingsPanel from '$components/ModelMusingsPanel.svelte';
  import MotionPanel from '$components/MotionPanel.svelte';
  import Panel from '$components/Panel.svelte';
  import PlaybackBar from '$components/PlaybackBar.svelte';
  import RecordingModal from '$components/RecordingModal.svelte';
  import SegmentationPanel from '$components/SegmentationPanel.svelte';
  import SensorPanel from '$components/SensorPanel.svelte';
  import SportPanel from '$components/SportPanel.svelte';
  import StreamCanvas from '$components/StreamCanvas.svelte';
  import { createDashboardController } from '$lib/controller';
  import type { DecodedFrameAsset, PanelId, RecordingManifest } from '$lib/types';

  const controller = createDashboardController();
  const recording = controller.recording;
  const playback = controller.playback;
  const connection = controller.connection;
  const progress = controller.progress;
  const currentFrame = controller.currentFrame;
  const panel = controller.panel;

  let showLoad = false;
  let showHealth = false;

  onMount(() => {
    void controller.initialize();
    const hash = window.location.hash.slice(1);
    if (['segmentation', 'motioncap', 'sport', 'sensors', 'localization'].includes(hash)) {
      void controller.setActivePanel(hash as PanelId);
    }
  });

  onDestroy(() => controller.dispose());

  function selectRecording(row: { name: string }) {
    showLoad = false;
    const found = $recording.recordings.find((record) => record.name === row.name);
    if (found) void controller.selectRecording(found);
  }

  function metrics(manifest: RecordingManifest | undefined, frame: DecodedFrameAsset | undefined) {
    return [
      { label: 'Position', value: frame ? `${frame.frameIndex + 1}` : '--' },
      { label: 'Source Frame', value: frame?.frameNumber ?? '--' },
      { label: 'FPS', value: manifest?.fps ? manifest.fps.toFixed(1) : '--' },
      { label: 'Source', value: manifest?.recordingId ?? '--' },
      { label: 'Device', value: frame?.metadata.deviceId || manifest?.deviceIds?.[0] || '--' },
      { label: 'Objects', value: $panel.segmentation.asset?.legend.length ?? 0 }
    ];
  }
</script>

<Header
  aggregate={$connection.aggregate}
  loading={$connection.loading}
  progress={$progress}
  onLoad={() => (showLoad = true)}
  onHealth={() => (showHealth = true)}
  onRefresh={() => controller.refreshHealth()}
/>

<main class="main-container">
  <PlaybackBar
    playing={$playback.playing}
    mode={$playback.mode}
    status={$playback.status}
    frameCount={$playback.frameCount}
    displayIndex={$playback.displayIndex}
    scrubIndex={$playback.scrubIndex}
    fps={$playback.fps}
    progress={$progress}
    onPlay={() => controller.play()}
    onPause={() => controller.pause()}
    onSkip={(delta) => controller.skip(delta)}
    onBeginScrub={() => controller.beginScrub()}
    onPreviewScrub={(index) => controller.previewScrub(index)}
    onCommitScrub={(index) => controller.commitSeek(index)}
  />

  <div class="top-analysis-grid">
    <Panel title="RGB Stream">
      <StreamCanvas frame={$currentFrame} label="RGB stream" />
    </Panel>
    <Panel>
      <ModelMusingsPanel data={$panel.model} recordingName={$recording.selected?.name ?? ''} />
    </Panel>
  </div>

  <MetricGrid metrics={metrics($recording.manifest, $currentFrame)} />

  <div class="workspace">
    <aside>
      <DashboardTabs active={$panel.active} onSelect={(id: PanelId) => controller.setActivePanel(id)} />
    </aside>
    <section class="active-panel" id={`panel-${$panel.active}`} role="tabpanel">
      {#if $panel.active === 'segmentation'}
        <SegmentationPanel
          frame={$currentFrame}
          state={$panel.segmentation.state}
          asset={$panel.segmentation.asset}
          error={$panel.segmentation.error ?? ''}
        />
      {:else if $panel.active === 'motioncap'}
        <MotionPanel
          frame={$currentFrame}
          state={$panel.motion.state}
          mode={$panel.motion.mode}
          asset={$panel.motion.asset}
          legendTracks={$panel.motion.legendTracks}
          inactiveTrackCount={$panel.motion.inactiveTrackCount}
          error={$panel.motion.error ?? ''}
          onMode={(mode) => controller.setMotionMode(mode)}
        />
      {:else if $panel.active === 'sport'}
        <SportPanel
          frame={$currentFrame}
          state={$panel.sport.state}
          mode={$panel.sport.mode}
          asset={$panel.sport.asset}
          state3d={$panel.sport.state3d}
          poseCorrections={$panel.sport.poseCorrections}
          error={$panel.sport.error ?? ''}
          onMode={(mode) => controller.setSportMode(mode)}
          onTogglePoseCorrections={() => controller.togglePoseCorrections()}
        />
      {:else if $panel.active === 'sensors'}
        <SensorPanel
          frame={$currentFrame}
          state={$panel.sensors.state}
          dataset={$panel.sensors.dataset}
          error={$panel.sensors.error ?? ''}
        />
      {:else}
        <LocalizationPanel
          frame={$currentFrame}
          state={$panel.localization.state}
          asset={$panel.localization.asset}
          segmentation={$panel.segmentation.asset}
          error={$panel.localization.error ?? ''}
        />
      {/if}
    </section>
  </div>
</main>

<RecordingModal
  open={showLoad}
  recordings={$recording.recordings}
  loading={$recording.loading}
  activeName={$recording.selected?.name ?? ''}
  onClose={() => (showLoad = false)}
  onSelect={selectRecording}
  onLive={() => {
    showLoad = false;
    controller.switchToLive();
  }}
  onUpload={(file) => controller.uploadRecording(file)}
/>

<EndpointHealthDialog
  open={showHealth}
  checks={$connection.checks}
  loading={$connection.loading}
  onClose={() => (showHealth = false)}
  onRefresh={() => controller.refreshHealth()}
/>
