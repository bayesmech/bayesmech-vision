import { get, writable, type Writable } from 'svelte/store';
import { StreamlogClient } from './streamlogClient';
import type {
  DecodedFrameAsset,
  EndpointCheck,
  LoadState,
  LocalizationAsset,
  ModelMusingsData,
  MotionOverlayAsset,
  MotionTrack,
  PanelId,
  PlaybackMode,
  PlaybackStatus,
  Pong3DState,
  PongOverlayAsset,
  PongOverlayMode,
  ProgressState,
  RecordingManifest,
  RecordingRow,
  SegmentationAsset,
  SensorDataset,
  WorkerRequest,
  WorkerResponse
} from './types';

type PlaybackState = {
  mode: PlaybackMode;
  status: PlaybackStatus;
  playing: boolean;
  frameCount: number;
  fps: number;
  targetIndex: number;
  displayIndex: number;
  scrubIndex: number;
  currentTimeS: number;
  durationS: number;
  buffering: boolean;
  liveFrameCount: number;
  error?: string;
};

type RecordingState = {
  selected?: RecordingRow;
  manifest?: RecordingManifest;
  recordings: RecordingRow[];
  loading: boolean;
  error?: string;
};

type ConnectionState = {
  aggregate: 'READY' | 'BUFFERING' | 'LIVE' | 'DEGRADED' | 'OFFLINE';
  checks: EndpointCheck[];
  loading: boolean;
  lastRefresh?: number;
};

type SegmentationState = {
  state: LoadState;
  asset?: SegmentationAsset;
  error?: string;
};

type MotionState = {
  state: LoadState;
  mode: 'raft' | 'segmentation';
  asset?: MotionOverlayAsset;
  legendTracks: MotionTrack[];
  inactiveTrackCount: number;
  error?: string;
};

type SportState = {
  state: LoadState;
  mode: PongOverlayMode;
  poseCorrections: boolean;
  asset?: PongOverlayAsset;
  state3d?: Pong3DState;
  error?: string;
};

type SensorState = {
  state: LoadState;
  dataset?: SensorDataset;
  error?: string;
};

type LocalizationState = {
  state: LoadState;
  asset?: LocalizationAsset;
  selectedPoint?: number;
  error?: string;
};

type PanelState = {
  active: PanelId;
  segmentation: SegmentationState;
  motion: MotionState;
  sport: SportState;
  sensors: SensorState;
  localization: LocalizationState;
  model: ModelMusingsData;
};

type WorkerBridge = {
  request<T>(type: string, payload: unknown, transfer?: Transferable[], priority?: WorkerRequest['priority']): Promise<T>;
  dispose(): void;
};

const EMPTY_PROGRESS: ProgressState = { visible: false, label: '', loaded: 0, total: 1 };

export class DashboardController {
  readonly client = new StreamlogClient();
  readonly recording: Writable<RecordingState> = writable({ recordings: [], loading: false });
  readonly playback: Writable<PlaybackState> = writable({
    mode: 'file',
    status: 'idle',
    playing: false,
    frameCount: 0,
    fps: 30,
    targetIndex: 0,
    displayIndex: 0,
    scrubIndex: 0,
    currentTimeS: 0,
    durationS: 0,
    buffering: false,
    liveFrameCount: 0
  });
  readonly connection: Writable<ConnectionState> = writable({ aggregate: 'OFFLINE', checks: [], loading: false });
  readonly progress: Writable<ProgressState> = writable(EMPTY_PROGRESS);
  readonly currentFrame: Writable<DecodedFrameAsset | undefined> = writable(undefined);
  readonly panel: Writable<PanelState> = writable({
    active: 'segmentation',
    segmentation: { state: 'idle' },
    motion: { state: 'idle', mode: 'raft', legendTracks: [], inactiveTrackCount: 0 },
    sport: { state: 'idle', mode: 'global', poseCorrections: true },
    sensors: { state: 'idle' },
    localization: { state: 'idle' },
    model: { status: 'idle', parameters: [], turns: [] }
  });

  private frameWorker = createBridge(new Worker(new URL('../workers/frameWorker.ts', import.meta.url), { type: 'module' }), () => this.recordingId, () => this.generation);
  private analysisWorker = createBridge(new Worker(new URL('../workers/analysisWorker.ts', import.meta.url), { type: 'module' }), () => this.recordingId, () => this.generation);
  private generation = 0;
  private recordingId = '';
  private aborters = new Set<AbortController>();
  private frameCache = new Map<number, DecodedFrameAsset>();
  private inflightRanges = new Map<string, Promise<void>>();
  private playRaf = 0;
  private playStartedAt = 0;
  private playStartIndex = 0;
  private wantedResume = false;
  private scrubGeneration = 0;
  private previewTimer = 0;
  private motionAssetInFlight = false;
  private motionAssetQueued = false;
  private motionHeatmapInFlight = false;
  private motionHeatmapQueued = false;
  private liveSocket?: WebSocket;
  private liveFrameCounter = 0;
  private segmentationRanges: Array<{ start: number; end: number }> = [];
  private segmentationInflight?: { start: number; end: number; promise: Promise<void> };

  async initialize() {
    await Promise.allSettled([this.refreshRecordings(), this.refreshHealth()]);
  }

  async refreshRecordings() {
    this.recording.update((state) => ({ ...state, loading: true, error: undefined }));
    try {
      const recordings = await this.client.listRecordings();
      this.recording.update((state) => ({ ...state, recordings, loading: false }));
    } catch (error) {
      this.recording.update((state) => ({ ...state, loading: false, error: errorMessage(error) }));
    }
  }

  async refreshHealth() {
    this.connection.update((state) => ({ ...state, loading: true }));
    const active = get(this.panel).active;
    const activeAnalysis = active === 'sport' ? 'pongtown' : active === 'localization' ? 'idoslam' : active;
    const checks = await this.client.checkEndpoints(this.recordingId || undefined, activeAnalysis).catch((error): EndpointCheck[] => [
      { name: 'Global health', kind: 'HTTP', url: '/streamlog/health', status: 'failed', detail: errorMessage(error) }
    ]);
    const failedRequired = checks.some((check) => check.status === 'failed' && ['Global health', 'Outstream health', 'Recording library'].includes(check.name));
    const failedOptional = checks.some((check) => check.status === 'failed');
    const playback = get(this.playback);
    const aggregate = playback.mode === 'live' && playback.playing ? 'LIVE' : playback.buffering ? 'BUFFERING' : failedRequired ? 'OFFLINE' : failedOptional ? 'DEGRADED' : 'READY';
    this.connection.set({ aggregate, checks, loading: false, lastRefresh: Date.now() });
  }

  async selectRecording(recording: RecordingRow) {
    this.stopPlayback(false);
    this.closeLiveSocket();
    this.abortAll();
    this.disposeFrames();
    this.generation += 1;
    this.recordingId = recording.name;
    this.inflightRanges.clear();
    this.segmentationRanges = [];
    this.segmentationInflight = undefined;
    this.currentFrame.set(undefined);
    this.panel.update((state) => ({
      ...state,
      segmentation: { ...state.segmentation, state: 'idle', asset: undefined, error: undefined },
      motion: { ...state.motion, state: 'idle', asset: undefined, legendTracks: [], inactiveTrackCount: 0, error: undefined },
      sport: { ...state.sport, state: 'idle', asset: undefined, state3d: undefined, error: undefined },
      sensors: { state: 'idle' },
      localization: { state: 'idle' },
      model: { status: 'loading', parameters: [], turns: [] }
    }));
    await Promise.allSettled([
      this.analysisWorker.request('reset-recording', {}, [], 'interactive'),
      this.frameWorker.request('reset-recording', {}, [], 'interactive')
    ]);
    this.recording.update((state) => ({ ...state, selected: recording, manifest: undefined, loading: true, error: undefined }));
    this.setProgress('Loading recording manifest', 0, 3);
    try {
      const manifest = await this.client.getRecordingManifest(recording.name, this.signal());
      this.recording.update((state) => ({ ...state, manifest, loading: false }));
      this.playback.update((state) => ({
        ...state,
        mode: 'file',
        status: 'buffering',
        playing: false,
        frameCount: manifest.frameCount,
        fps: manifest.fps || 30,
        targetIndex: 0,
        displayIndex: 0,
        scrubIndex: 0,
        currentTimeS: 0,
        durationS: durationSeconds(manifest),
        buffering: true,
        error: undefined
      }));
      this.setProgress('Decoding first frame', 1, 3, `${manifest.frameCount} frames`);
      await this.ensureFrameRange(0, Math.min(manifest.frameCount, 12), 'interactive');
      const first = this.frameCache.get(0) ?? [...this.frameCache.values()][0];
      if (first) this.displayFrame(first);
      this.setProgress('Priming playback cache', 2, 3);
      void this.ensureFrameWindow(0, 2, 48, 'prefetch');
      void this.loadModelMusings(recording.name);
      this.playback.update((state) => ({ ...state, status: 'paused', buffering: false }));
      this.clearProgress();
      await this.refreshHealth();
    } catch (error) {
      this.clearProgress();
      this.recording.update((state) => ({ ...state, loading: false, error: errorMessage(error) }));
      this.playback.update((state) => ({ ...state, status: 'error', playing: false, buffering: false, error: errorMessage(error) }));
    }
  }

  async play() {
    const state = get(this.playback);
    if (state.mode === 'live') {
      this.playback.update((current) => ({ ...current, playing: true, status: 'playing' }));
      return;
    }
    if (!this.recordingId || state.playing || state.frameCount <= 0) return;
    const target = clampIndex(state.scrubIndex || state.displayIndex, state.frameCount);
    this.setProgress('Preparing playback layers', 0, 2);
    try {
      await this.ensurePlaybackPrerequisites();
      await this.ensureFrameWindow(target, 0, Math.ceil(state.fps * 2.5), 'playback');
      this.clearProgress();
      this.playStartIndex = target;
      this.playStartedAt = performance.now();
      this.playback.update((current) => ({ ...current, playing: true, status: 'playing', buffering: false, targetIndex: target, scrubIndex: target }));
      this.tickPlayback();
    } catch (error) {
      this.playback.update((current) => ({ ...current, playing: false, status: 'error', buffering: false, error: errorMessage(error) }));
      this.clearProgress();
    }
  }

  pause() {
    if (get(this.playback).mode === 'live') {
      this.playback.update((state) => ({ ...state, playing: false, status: 'paused' }));
    } else {
      this.stopPlayback(false);
    }
  }

  async switchToLive() {
    this.stopPlayback(false);
    this.abortAll();
    this.disposeFrames();
    this.closeLiveSocket();
    this.generation += 1;
    this.recordingId = 'live';
    this.liveFrameCounter = 0;
    this.currentFrame.set(undefined);
    this.recording.update((state) => ({ ...state, selected: undefined, manifest: undefined }));
    this.playback.set({
      mode: 'live',
      status: 'buffering',
      playing: true,
      frameCount: 0,
      fps: 30,
      targetIndex: 0,
      displayIndex: 0,
      scrubIndex: 0,
      currentTimeS: 0,
      durationS: 0,
      buffering: true,
      liveFrameCount: 0
    });
    this.setProgress('Connecting live stream', 0, 1);
    try {
      await this.client.startLivePlayback(this.signal());
      this.liveSocket = new WebSocket(this.client.dashboardWebSocketUrl());
      this.liveSocket.binaryType = 'arraybuffer';
      this.liveSocket.onopen = () => {
        this.clearProgress();
        this.playback.update((state) => ({ ...state, status: 'playing', buffering: false }));
      };
      this.liveSocket.onclose = () => {
        this.playback.update((state) => ({ ...state, playing: false, status: 'stalled', buffering: false }));
      };
      this.liveSocket.onerror = () => {
        this.playback.update((state) => ({ ...state, status: 'error', error: 'Live WebSocket failed' }));
      };
      this.liveSocket.onmessage = (event) => {
        void this.handleLiveMessage(event.data);
      };
      await this.refreshHealth();
    } catch (error) {
      this.clearProgress();
      this.playback.update((state) => ({ ...state, playing: false, status: 'error', buffering: false, error: errorMessage(error) }));
    }
  }

  skip(delta: number) {
    const state = get(this.playback);
    void this.commitSeek(clampIndex(state.displayIndex + delta, state.frameCount), false);
  }

  beginScrub() {
    const wasPlaying = get(this.playback).playing;
    this.wantedResume = wasPlaying;
    this.scrubGeneration += 1;
    this.stopPlayback(false);
    this.playback.update((state) => ({ ...state, status: 'seeking' }));
  }

  previewScrub(index: number) {
    const state = get(this.playback);
    const frameIndex = clampIndex(index, state.frameCount);
    this.playback.update((current) => ({ ...current, scrubIndex: frameIndex, targetIndex: frameIndex, currentTimeS: frameIndex / Math.max(current.fps, 1) }));
    window.clearTimeout(this.previewTimer);
    const scrubId = this.scrubGeneration;
    this.previewTimer = window.setTimeout(() => {
      if (scrubId !== this.scrubGeneration) return;
      void this.ensureFrameRange(frameIndex, frameIndex + 1, 'interactive').then(() => {
        if (scrubId !== this.scrubGeneration) return;
        const frame = this.frameCache.get(frameIndex);
        if (frame) this.displayFrame(frame);
      });
    }, 34);
  }

  async commitSeek(index: number, restorePlaying = this.wantedResume) {
    const state = get(this.playback);
    const frameIndex = clampIndex(index, state.frameCount);
    this.stopPlayback(false);
    this.setProgress('Seeking frame', 0, 2, `#${frameIndex}`);
    this.playback.update((current) => ({ ...current, status: 'seeking', buffering: true, targetIndex: frameIndex, scrubIndex: frameIndex }));
    try {
      await this.ensureFrameRange(frameIndex, frameIndex + 1, 'interactive');
      const frame = this.frameCache.get(frameIndex);
      if (frame) this.displayFrame(frame);
      this.setProgress('Refreshing seek window', 1, 2);
      void this.ensureFrameWindow(frameIndex, 15, 45, 'prefetch');
      this.clearProgress();
      this.playback.update((current) => ({ ...current, status: 'paused', buffering: false }));
      if (restorePlaying) await this.play();
    } catch (error) {
      this.clearProgress();
      this.playback.update((current) => ({ ...current, status: 'error', buffering: false, error: errorMessage(error) }));
    }
  }

  async setActivePanel(panel: PanelId) {
    const wasPlaying = get(this.playback).playing;
    if (wasPlaying) this.stopPlayback(false);
    this.panel.update((state) => ({ ...state, active: panel }));
    await this.loadPanelData(panel, wasPlaying);
    await this.refreshCurrentPanelAssets();
    if (wasPlaying) await this.play();
  }

  async setMotionMode(mode: MotionState['mode']) {
    this.panel.update((state) => ({ ...state, motion: { ...state.motion, mode } }));
    await this.refreshMotionAsset();
  }

  async setSportMode(mode: PongOverlayMode) {
    this.panel.update((state) => ({ ...state, sport: { ...state.sport, mode } }));
    await this.refreshPongAsset();
  }

  togglePoseCorrections() {
    this.panel.update((state) => ({ ...state, sport: { ...state.sport, poseCorrections: !state.sport.poseCorrections } }));
  }

  async uploadRecording(file: File) {
    this.setProgress('Uploading recording', 0, file.size, file.name);
    await this.client.startImport(file, this.signal(), (loaded, total) => this.setProgress('Uploading recording', loaded, total, file.name));
    this.clearProgress();
    await this.refreshRecordings();
  }

  dispose() {
    this.stopPlayback(false);
    this.closeLiveSocket();
    this.abortAll();
    this.disposeFrames();
    this.frameWorker.dispose();
    this.analysisWorker.dispose();
  }

  private tickPlayback = () => {
    this.playRaf = window.requestAnimationFrame(this.tickPlayback);
    const playback = get(this.playback);
    if (!playback.playing) return;
    const elapsed = (performance.now() - this.playStartedAt) / 1000;
    const target = clampIndex(Math.floor(this.playStartIndex + elapsed * playback.fps), playback.frameCount);
    if (target >= playback.frameCount - 1) {
      this.stopPlayback(false);
      void this.commitSeek(playback.frameCount - 1, false);
      return;
    }
    const frame = this.frameCache.get(target) ?? nearestPast(this.frameCache, target, Math.max(2, Math.round(playback.fps / 8)));
    if (frame) {
      if (frame.frameIndex !== playback.displayIndex) this.displayFrame(frame);
      this.playback.update((state) => ({ ...state, targetIndex: target, scrubIndex: target, currentTimeS: target / Math.max(state.fps, 1) }));
      if (target % 8 === 0) void this.ensureFrameWindow(target, 0, Math.ceil(playback.fps * 3), 'prefetch');
      return;
    }
    this.stopForBuffer(target);
  };

  private async stopForBuffer(target: number) {
    this.stopPlayback(true);
    this.setProgress('Buffering decoded frames', 0, 2, `#${target}`);
    this.playback.update((state) => ({ ...state, status: 'buffering', buffering: true, targetIndex: target }));
    try {
      await this.ensureFrameWindow(target, 1, Math.ceil(get(this.playback).fps * 2), 'playback');
      const frame = this.frameCache.get(target) ?? nearestPast(this.frameCache, target, 10);
      if (frame) this.displayFrame(frame);
      this.clearProgress();
      this.playback.update((state) => ({ ...state, buffering: false, status: 'paused' }));
      if (this.wantedResume) await this.play();
    } catch (error) {
      this.clearProgress();
      this.playback.update((state) => ({ ...state, status: 'stalled', buffering: false, error: errorMessage(error) }));
    }
  }

  private async handleLiveMessage(data: Blob | ArrayBuffer | string) {
    if (typeof data === 'string') return;
    const raw = data instanceof Blob ? await data.arrayBuffer() : data;
    const bytes = new Uint8Array(raw);
    if (bytes[0] !== 0x01) return;
    const payload = bytes.buffer.slice(bytes.byteOffset + 1, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    const frameIndex = this.liveFrameCounter;
    const response = await this.frameWorker
      .request<{ frames: DecodedFrameAsset[] }>(
        'decode-frame-range',
        {
          bytes: payload,
          metas: [{ frame_index: frameIndex, frame_number: frameIndex, timestamp_ns: Date.now() * 1_000_000, relative_timestamp_ns: 0, selector_match_delta_ns: 0 }],
          delimited: true
        },
        [payload],
        'playback'
      )
      .catch(() => ({ frames: [] }));
    for (const frame of response.frames) {
      frame.frameIndex = this.liveFrameCounter;
      frame.metadata.frameIndex = this.liveFrameCounter;
      this.liveFrameCounter += 1;
      if (get(this.playback).playing) this.displayFrame(frame);
    }
    this.playback.update((state) => ({ ...state, liveFrameCount: this.liveFrameCounter, frameCount: this.liveFrameCounter }));
  }

  private stopPlayback(rememberIntent: boolean) {
    if (rememberIntent) this.wantedResume = get(this.playback).playing;
    window.cancelAnimationFrame(this.playRaf);
    this.playRaf = 0;
    this.playback.update((state) => ({ ...state, playing: false, status: state.status === 'idle' ? 'idle' : 'paused' }));
  }

  private async ensurePlaybackPrerequisites() {
    const active = get(this.panel).active;
    await this.loadPanelData(active, true);
    const displayIndex = get(this.playback).displayIndex;
    await this.ensureFrameWindow(displayIndex, 0, Math.ceil(get(this.playback).fps * 1.5), 'playback');
  }

  private async loadPanelData(panel: PanelId, blocking: boolean) {
    if (!this.recordingId) return;
    if (panel === 'segmentation') {
      const frame = get(this.currentFrame);
      if (frame) await this.loadSegmentationWindow(frame.frameNumber, blocking);
      return;
    }
    if (panel === 'motioncap') return this.loadMotioncap(blocking);
    if (panel === 'sport') return this.loadPongtown(blocking);
    if (panel === 'sensors') return this.loadSensors(blocking);
    if (panel === 'localization') {
      const frame = get(this.currentFrame);
      await Promise.all([this.loadLocalization(blocking), frame ? this.loadSegmentationWindow(frame.frameNumber, blocking) : Promise.resolve()]);
      return;
    }
  }

  private async loadSegmentationWindow(frameNumber: number, blocking: boolean) {
    const state = get(this.panel).segmentation;
    const start = Math.max(0, frameNumber - 12);
    const end = frameNumber + 42;
    const usefulEnd = frameNumber + 24;
    if (this.hasSegmentationCoverage(frameNumber, usefulEnd)) return;
    if (this.segmentationInflight && this.segmentationInflight.start <= frameNumber && this.segmentationInflight.end >= usefulEnd) {
      return blocking ? this.segmentationInflight.promise : undefined;
    }

    const showPanelLoading = blocking || state.state === 'idle';
    if (showPanelLoading) {
      this.panel.update((panel) => ({ ...panel, segmentation: { ...panel.segmentation, state: 'loading', error: undefined } }));
    } else {
      this.panel.update((panel) => ({ ...panel, segmentation: { ...panel.segmentation, error: undefined } }));
    }
    if (blocking) this.setProgress('Loading segmentation window', 0, 1);

    const task = (async () => {
      const bytes = await this.client.fetchSegmentationRange(this.recordingId, start, end, this.signal());
      let decodedCount = 0;
      if (bytes) {
        const decoded = await this.analysisWorker.request<{ count: number }>('decode-segmentation', { bytes }, [bytes], 'playback');
        decodedCount = decoded.count;
      }
      this.addSegmentationRange(start, end);
      this.panel.update((panel) => ({
        ...panel,
        segmentation: {
          ...panel.segmentation,
          state: decodedCount > 0 ? 'ready' : panel.segmentation.state === 'ready' ? 'ready' : 'empty'
        }
      }));
      await this.refreshSegmentationAsset();
    })();

    this.segmentationInflight = { start, end, promise: task };
    try {
      await task;
    } catch (error) {
      if (showPanelLoading) {
        this.panel.update((panel) => ({ ...panel, segmentation: { ...panel.segmentation, state: 'error', error: errorMessage(error) } }));
      } else {
        this.panel.update((panel) => ({ ...panel, segmentation: { ...panel.segmentation, error: errorMessage(error) } }));
      }
    } finally {
      if (this.segmentationInflight?.promise === task) this.segmentationInflight = undefined;
      if (blocking) this.clearProgress();
    }
  }

  private hasSegmentationCoverage(frameNumber: number, usefulEnd: number) {
    return this.segmentationRanges.some((range) => range.start <= frameNumber && range.end >= usefulEnd);
  }

  private addSegmentationRange(start: number, end: number) {
    const ranges = [...this.segmentationRanges, { start, end }].sort((a, b) => a.start - b.start);
    const merged: Array<{ start: number; end: number }> = [];
    for (const range of ranges) {
      const last = merged.at(-1);
      if (!last || range.start > last.end + 1) {
        merged.push({ ...range });
      } else {
        last.end = Math.max(last.end, range.end);
      }
    }
    this.segmentationRanges = merged;
  }

  private async loadMotioncap(blocking: boolean) {
    const state = get(this.panel).motion;
    if (state.state === 'ready' || state.state === 'loading') return;
    this.panel.update((panel) => ({ ...panel, motion: { ...panel.motion, state: 'loading', error: undefined } }));
    if (blocking) this.setProgress('Precomputing motion capture', 0, 2);
    try {
      const bytes = await this.client.fetchAnalysisRecords(this.recordingId, 'motioncap', 'proto', this.signal(), true);
      if (!bytes) {
        this.panel.update((panel) => ({ ...panel, motion: { ...panel.motion, state: 'empty' } }));
        return;
      }
      if (blocking) this.setProgress('Decoding motion tracks', 1, 2);
      const response = await this.analysisWorker.request<{ tracks: MotionTrack[]; segmentationTracks: MotionTrack[]; frameCount: number }>(
        'decode-motioncap',
        { bytes },
        [bytes],
        'background'
      );
      this.panel.update((panel) => ({
        ...panel,
        motion: {
          ...panel.motion,
          state: response.frameCount || response.tracks.length || response.segmentationTracks.length ? 'ready' : 'empty',
          legendTracks: panel.motion.mode === 'raft' ? response.tracks : response.segmentationTracks,
          inactiveTrackCount: panel.motion.mode === 'raft' ? response.segmentationTracks.length : response.tracks.length
        }
      }));
      await this.refreshMotionAsset();
    } catch (error) {
      this.panel.update((panel) => ({ ...panel, motion: { ...panel.motion, state: 'error', error: errorMessage(error) } }));
    } finally {
      if (blocking) this.clearProgress();
    }
  }

  private async loadPongtown(blocking: boolean) {
    const state = get(this.panel).sport;
    if (state.state === 'ready' || state.state === 'loading') return;
    this.panel.update((panel) => ({ ...panel, sport: { ...panel.sport, state: 'loading', error: undefined } }));
    if (blocking) this.setProgress('Precomputing sport understanding', 0, 2);
    try {
      const bytes = await this.client.fetchAnalysisRecords(this.recordingId, 'pongtown', 'proto', this.signal(), true);
      if (!bytes) {
        this.panel.update((panel) => ({ ...panel, sport: { ...panel.sport, state: 'empty' } }));
        return;
      }
      if (blocking) this.setProgress('Building sport overlays', 1, 2);
      const response = await this.analysisWorker.request<{ state3d?: Pong3DState; frameCount: number }>('decode-pongtown', { bytes }, [bytes], 'background');
      this.panel.update((panel) => ({
        ...panel,
        sport: { ...panel.sport, state: response.frameCount ? 'ready' : 'empty', state3d: response.state3d }
      }));
      await this.refreshPongAsset();
    } catch (error) {
      this.panel.update((panel) => ({ ...panel, sport: { ...panel.sport, state: 'error', error: errorMessage(error) } }));
    } finally {
      if (blocking) this.clearProgress();
    }
  }

  private async loadSensors(blocking: boolean) {
    const state = get(this.panel).sensors;
    if (state.state === 'ready' || state.state === 'loading') return;
    this.panel.update((panel) => ({ ...panel, sensors: { state: 'loading' } }));
    if (blocking) this.setProgress('Loading full sensor timeline', 0, 1);
    try {
      const dataset = await this.client.fetchSensorDataset(this.recordingId, this.signal());
      this.panel.update((panel) => ({ ...panel, sensors: { state: dataset.frames.length || dataset.trajectory.length ? 'ready' : 'empty', dataset } }));
    } catch (error) {
      this.panel.update((panel) => ({ ...panel, sensors: { state: 'error', error: errorMessage(error) } }));
    } finally {
      if (blocking) this.clearProgress();
    }
  }

  private async loadLocalization(blocking: boolean) {
    const state = get(this.panel).localization;
    if (state.state === 'ready' || state.state === 'loading') return;
    this.panel.update((panel) => ({ ...panel, localization: { state: 'loading' } }));
    if (blocking) this.setProgress('Precomputing localization map', 0, 2);
    try {
      const bytes = await this.client.fetchAnalysisArtifact(this.recordingId, 'idoslam', 'proto', this.signal());
      if (!bytes) {
        this.panel.update((panel) => ({ ...panel, localization: { state: 'empty' } }));
        return;
      }
      if (blocking) this.setProgress('Indexing SLAM poses', 1, 2);
      const response = await this.analysisWorker.request<{ asset: LocalizationAsset }>('decode-localization', { bytes }, [bytes], 'background');
      this.panel.update((panel) => ({ ...panel, localization: { state: response.asset.rawPoses.length || response.asset.refinedPoses.length ? 'ready' : 'empty', asset: response.asset } }));
    } catch (error) {
      this.panel.update((panel) => ({ ...panel, localization: { state: 'error', error: errorMessage(error) } }));
    } finally {
      if (blocking) this.clearProgress();
    }
  }

  private async loadModelMusings(recordingId: string) {
    this.panel.update((panel) => ({ ...panel, model: { status: 'loading', parameters: [], turns: [] } }));
    try {
      const [summaryBytes, chatBytes] = await Promise.all([this.client.fetchSummary(recordingId, this.signal()), this.client.fetchChat(recordingId, 0, this.signal())]);
      const transfer = [summaryBytes, chatBytes].filter(Boolean) as ArrayBuffer[];
      const response = await this.analysisWorker.request<{ data: ModelMusingsData }>(
        'decode-model',
        { summaryBytes, chatBytes },
        transfer,
        'background'
      );
      this.panel.update((panel) => ({ ...panel, model: response.data }));
    } catch (error) {
      this.panel.update((panel) => ({ ...panel, model: { status: 'error', parameters: [], turns: [], error: errorMessage(error) } }));
    }
  }

  private async refreshCurrentPanelAssets() {
    const active = get(this.panel).active;
    if (active === 'segmentation') await this.refreshSegmentationAsset();
    if (active === 'motioncap') await this.refreshMotionAsset();
    if (active === 'sport') await this.refreshPongAsset();
    if (active === 'localization') await this.refreshSegmentationAsset();
  }

  private async refreshSegmentationAsset() {
    const frame = get(this.currentFrame);
    if (!frame || !this.recordingId) return;
    const panel = get(this.panel).segmentation;
    if (panel.state === 'idle') return;
    const response = await this.analysisWorker
      .request<{ asset?: SegmentationAsset }>(
        'get-segmentation',
        { frameNumber: frame.frameNumber, matchMode: 'floor', toleranceFrames: 30 },
        [],
        'playback'
      )
      .catch(() => ({ asset: undefined }));
    this.panel.update((state) => ({ ...state, segmentation: { ...state.segmentation, asset: response.asset } }));
  }

  private async refreshMotionAsset() {
    if (this.motionAssetInFlight) {
      this.motionAssetQueued = true;
      return;
    }
    this.motionAssetInFlight = true;
    try {
      while (true) {
        this.motionAssetQueued = false;
        const frame = get(this.currentFrame);
        const panel = get(this.panel).motion;
        if (!frame || panel.state !== 'ready') break;
        const requestedFrameIndex = frame.frameIndex;
        const requestedFrameNumber = frame.frameNumber;
        const requestedMode = panel.mode;
        const response = await this.analysisWorker
          .request<{ asset?: MotionOverlayAsset; legendTracks?: MotionTrack[]; inactiveTrackCount?: number }>(
            'get-motioncap',
            { frameNumber: requestedFrameNumber, frameIndex: requestedFrameIndex, mode: requestedMode },
            [],
            'playback'
          )
          .catch((): { asset?: MotionOverlayAsset; legendTracks?: MotionTrack[]; inactiveTrackCount?: number } => ({ asset: undefined }));
        const latestFrame = get(this.currentFrame);
        const latestMotion = get(this.panel).motion;
        if (latestFrame?.frameIndex === requestedFrameIndex && latestFrame.frameNumber === requestedFrameNumber && latestMotion.mode === requestedMode) {
          this.panel.update((state) => ({
            ...state,
            motion: {
              ...state.motion,
              asset: mergeMotionHeatmapForFrame(state.motion.asset, response.asset),
              legendTracks: response.legendTracks ?? state.motion.legendTracks,
              inactiveTrackCount: response.inactiveTrackCount ?? state.motion.inactiveTrackCount
            }
          }));
          void this.refreshMotionHeatmap();
        }
        if (!this.motionAssetQueued) break;
      }
    } finally {
      this.motionAssetInFlight = false;
      if (this.motionAssetQueued) void this.refreshMotionAsset();
    }
  }

  private async refreshMotionHeatmap() {
    if (this.motionHeatmapInFlight) {
      this.motionHeatmapQueued = true;
      return;
    }
    this.motionHeatmapInFlight = true;
    try {
      while (true) {
        this.motionHeatmapQueued = false;
        const frame = get(this.currentFrame);
        const panel = get(this.panel).motion;
        if (!frame || panel.state !== 'ready') break;
        const requestedFrameIndex = frame.frameIndex;
        const requestedFrameNumber = frame.frameNumber;
        type MotionHeatmapResponse = {
          frameNumber: number;
          frameIndex: number;
          heatmapBitmap?: ImageBitmap;
          heatmapWidth?: number;
          heatmapHeight?: number;
        };
        const response: MotionHeatmapResponse = await this.analysisWorker
          .request<{
            frameNumber: number;
            frameIndex: number;
            heatmapBitmap?: ImageBitmap;
            heatmapWidth?: number;
            heatmapHeight?: number;
          }>(
            'get-motioncap-heatmap',
            { frameNumber: requestedFrameNumber, frameIndex: requestedFrameIndex, maxWidth: 640 },
            [],
            'playback'
          )
          .catch((): MotionHeatmapResponse => ({ frameNumber: requestedFrameNumber, frameIndex: requestedFrameIndex }));
        const latestFrame = get(this.currentFrame);
        const latestMotion = get(this.panel).motion;
        if (latestFrame?.frameIndex === requestedFrameIndex && latestFrame.frameNumber === requestedFrameNumber && latestMotion.state === 'ready') {
          this.panel.update((state) => {
            const currentAsset = state.motion.asset;
            if (!currentAsset || currentAsset.frameIndex !== requestedFrameIndex || currentAsset.frameNumber !== requestedFrameNumber) return state;
            const previousBitmap = currentAsset.heatmapBitmap;
            if (previousBitmap && previousBitmap !== response.heatmapBitmap) previousBitmap.close();
            return {
              ...state,
              motion: {
                ...state.motion,
                asset: {
                  ...currentAsset,
                  heatmapBitmap: response.heatmapBitmap,
                  heatmapWidth: response.heatmapWidth,
                  heatmapHeight: response.heatmapHeight
                }
              }
            };
          });
        } else if (response.heatmapBitmap) {
          response.heatmapBitmap.close();
        }
        if (!this.motionHeatmapQueued) break;
      }
    } finally {
      this.motionHeatmapInFlight = false;
      if (this.motionHeatmapQueued) void this.refreshMotionHeatmap();
    }
  }

  private async refreshPongAsset() {
    const frame = get(this.currentFrame);
    const panel = get(this.panel).sport;
    if (!frame || panel.state !== 'ready') return;
    const response = await this.analysisWorker
      .request<{ asset?: PongOverlayAsset; state3d?: Pong3DState }>(
        'get-pongtown',
        { frameNumber: frame.frameNumber, frameIndex: frame.frameIndex, mode: panel.mode },
        [],
        'playback'
      )
      .catch((): { asset?: PongOverlayAsset; state3d?: Pong3DState } => ({ asset: undefined }));
    this.panel.update((state) => ({ ...state, sport: { ...state.sport, asset: response.asset, state3d: response.state3d ?? state.sport.state3d } }));
  }

  private displayFrame(frame: DecodedFrameAsset) {
    this.currentFrame.set(frame);
    this.playback.update((state) => ({
      ...state,
      displayIndex: frame.frameIndex,
      scrubIndex: frame.frameIndex,
      targetIndex: frame.frameIndex,
      currentTimeS: frame.frameIndex / Math.max(state.fps, 1),
      status: state.playing ? 'playing' : state.status === 'buffering' ? 'buffering' : 'paused'
    }));
    const active = get(this.panel).active;
    if (active === 'segmentation') void this.refreshSegmentationAsset();
    if (active === 'motioncap') void this.refreshMotionAsset();
    if (active === 'sport') void this.refreshPongAsset();
    if (active === 'localization') void this.refreshSegmentationAsset();
    if (active === 'segmentation' && get(this.playback).mode === 'file') void this.loadSegmentationWindow(frame.frameNumber, false);
    if (active === 'localization' && get(this.playback).mode === 'file') void this.loadSegmentationWindow(frame.frameNumber, false);
  }

  private async ensureFrameWindow(center: number, before: number, after: number, priority: WorkerRequest['priority']) {
    const frameCount = get(this.playback).frameCount;
    const start = Math.max(0, center - before);
    const end = Math.min(frameCount, center + after + 1);
    await this.ensureMissingRanges(start, end, priority);
  }

  private async ensureMissingRanges(start: number, end: number, priority: WorkerRequest['priority']) {
    let cursor = start;
    const promises: Promise<void>[] = [];
    while (cursor < end) {
      while (cursor < end && this.frameCache.has(cursor)) cursor += 1;
      if (cursor >= end) break;
      let rangeEnd = cursor + 1;
      while (rangeEnd < end && !this.frameCache.has(rangeEnd) && rangeEnd - cursor < 18) rangeEnd += 1;
      promises.push(this.ensureFrameRange(cursor, rangeEnd, priority));
      cursor = rangeEnd;
    }
    await Promise.all(promises);
  }

  private async ensureFrameRange(start: number, end: number, priority: WorkerRequest['priority']) {
    if (!this.recordingId || start >= end) return;
    const key = `${this.generation}:${start}:${end}`;
    if (this.inflightRanges.has(key)) return this.inflightRanges.get(key);
    const task = (async () => {
      const generation = this.generation;
      const controller = this.abortController();
      const { bytes, metas } = end - start === 1 ? await this.client.fetchFrame(this.recordingId, start, controller.signal) : await this.client.fetchFrameRange(this.recordingId, start, end, controller.signal);
      if (generation !== this.generation) return;
      const response = await this.frameWorker.request<{ frames: DecodedFrameAsset[] }>(
        'decode-frame-range',
        { bytes, metas, delimited: end - start > 1 },
        [bytes],
        priority
      );
      if (generation !== this.generation) {
        response.frames.forEach((frame) => frame.rgbBitmap?.close());
        return;
      }
      for (const frame of response.frames) {
        this.frameCache.set(frame.frameIndex, frame);
      }
      this.evictFrameCache();
    })().finally(() => {
      this.inflightRanges.delete(key);
    });
    this.inflightRanges.set(key, task);
    return task;
  }

  private evictFrameCache() {
    const current = get(this.playback).displayIndex;
    while (this.frameCache.size > 260) {
      const [index, frame] = this.frameCache.entries().next().value as [number, DecodedFrameAsset];
      if (Math.abs(index - current) < 4) {
        this.frameCache.delete(index);
        this.frameCache.set(index, frame);
        continue;
      }
      frame.rgbBitmap?.close();
      frame.depthBitmap?.close();
      this.frameCache.delete(index);
    }
  }

  private signal(): AbortSignal {
    return this.abortController().signal;
  }

  private abortController(): AbortController {
    const controller = new AbortController();
    this.aborters.add(controller);
    controller.signal.addEventListener('abort', () => this.aborters.delete(controller), { once: true });
    return controller;
  }

  private abortAll() {
    this.aborters.forEach((controller) => controller.abort());
    this.aborters.clear();
  }

  private closeLiveSocket() {
    if (this.liveSocket) {
      this.liveSocket.onopen = null;
      this.liveSocket.onclose = null;
      this.liveSocket.onerror = null;
      this.liveSocket.onmessage = null;
      this.liveSocket.close();
    }
    this.liveSocket = undefined;
  }

  private disposeFrames() {
    this.frameCache.forEach((frame) => {
      frame.rgbBitmap?.close();
      frame.depthBitmap?.close();
    });
    this.frameCache.clear();
  }

  private setProgress(label: string, loaded: number, total: number, detail?: string) {
    this.progress.set({ visible: true, label, loaded, total: Math.max(total, 1), detail });
  }

  private clearProgress() {
    this.progress.set(EMPTY_PROGRESS);
  }
}

function createBridge(worker: Worker, recordingId: () => string, generation: () => number): WorkerBridge {
  let requestId = 0;
  const pending = new Map<number, { resolve: (value: any) => void; reject: (error: Error) => void }>();
  worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
    const response = event.data;
    const handler = pending.get(response.requestId);
    if (!handler) return;
    pending.delete(response.requestId);
    if (!response.ok) handler.reject(new Error(response.error ?? 'Worker request failed'));
    else handler.resolve(response.payload);
  };
  worker.onerror = (event) => {
    const error = new Error(event.message || 'Worker crashed');
    pending.forEach((handler) => handler.reject(error));
    pending.clear();
  };
  return {
    request<T>(type: string, payload: unknown, transfer: Transferable[] = [], priority: WorkerRequest['priority'] = 'background') {
      requestId += 1;
      const message: WorkerRequest = {
        requestId,
        recordingId: recordingId(),
        generation: generation(),
        priority,
        type,
        payload
      };
      return new Promise<T>((resolve, reject) => {
        pending.set(requestId, { resolve, reject });
        worker.postMessage(message, transfer);
      });
    },
    dispose() {
      worker.terminate();
      pending.clear();
    }
  };
}

function mergeMotionHeatmapForFrame(previous: MotionOverlayAsset | undefined, next: MotionOverlayAsset | undefined): MotionOverlayAsset | undefined {
  if (!next) {
    previous?.heatmapBitmap?.close();
    return undefined;
  }
  if (previous?.frameIndex === next.frameIndex && previous.frameNumber === next.frameNumber) {
    return {
      ...next,
      heatmapBitmap: previous.heatmapBitmap,
      heatmapWidth: previous.heatmapWidth,
      heatmapHeight: previous.heatmapHeight
    };
  }
  return {
    ...next,
    heatmapBitmap: previous?.heatmapBitmap,
    heatmapWidth: previous?.heatmapWidth,
    heatmapHeight: previous?.heatmapHeight
  };
}

function nearestPast(cache: Map<number, DecodedFrameAsset>, target: number, tolerance: number): DecodedFrameAsset | undefined {
  for (let index = target; index >= Math.max(0, target - tolerance); index -= 1) {
    const frame = cache.get(index);
    if (frame) return frame;
  }
  return undefined;
}

function durationSeconds(manifest: RecordingManifest): number {
  if (manifest.durationNs > 0) return manifest.durationNs / 1_000_000_000;
  return manifest.frameCount / Math.max(manifest.fps, 1);
}

function clampIndex(index: number, frameCount: number): number {
  if (frameCount <= 0) return 0;
  return Math.max(0, Math.min(frameCount - 1, Math.round(index)));
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return 'Request aborted';
  return error instanceof Error ? error.message : String(error);
}

export function createDashboardController() {
  return new DashboardController();
}
