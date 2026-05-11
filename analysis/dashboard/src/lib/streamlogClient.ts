import type {
  AnalysisAvailability,
  EndpointCheck,
  FrameResponseMeta,
  RawRecordingManifest,
  RecordingManifest,
  RecordingRow,
  SensorDataset,
  SensorFrame,
  TrajectoryPoint
} from './types';

export class StreamlogHttpError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = 'StreamlogHttpError';
    this.status = status;
    this.detail = detail;
  }
}

type RequestOptions = {
  signal?: AbortSignal;
  timeoutMs?: number;
  headers?: HeadersInit;
};

export class StreamlogClient {
  readonly basePath: string;

  constructor(basePath = '/streamlog') {
    this.basePath = basePath.replace(/\/$/, '');
  }

  async health(signal?: AbortSignal): Promise<unknown> {
    return this.getJson(`${this.basePath}/health`, { signal });
  }

  async planeHealth(plane: 'outstream' | 'instream' | 'insightgen' | 'analyzers', signal?: AbortSignal): Promise<unknown> {
    return this.getJson(`${this.basePath}/${plane}/health`, { signal });
  }

  async listRecordings(signal?: AbortSignal): Promise<RecordingRow[]> {
    const data = await this.getJson<{ recordings?: RecordingRow[] }>(`${this.basePath}/insightgen/recordings`, { signal });
    return Array.isArray(data.recordings) ? data.recordings : [];
  }

  async getRecordingManifest(recordingId: string, signal?: AbortSignal): Promise<RecordingManifest> {
    const encoded = encodeURIComponent(recordingId);
    const data = await this.getJson<{
      manifest?: RawRecordingManifest;
      analysis?: { analyses?: AnalysisAvailability[] };
    }>(`${this.basePath}/insightgen/recordings/${encoded}`, { signal });
    const manifest = data.manifest ?? {};
    const analyses = data.analysis?.analyses ?? [];
    return {
      recordingId: manifest.recording_id ?? recordingId,
      frameCount: manifest.frame_count ?? 0,
      fps: finiteOr(manifest.estimated_fps, 30),
      firstTimestampNs: manifest.first_timestamp_ns ?? 0,
      lastTimestampNs: manifest.last_timestamp_ns ?? 0,
      durationNs: manifest.duration_ns ?? 0,
      deviceIds: manifest.device_ids ?? [],
      sourceSizeBytes: manifest.source_size_bytes ?? 0,
      analyses,
      status: manifest.status
    };
  }

  async getAnalysisIndex(recordingId: string, signal?: AbortSignal): Promise<AnalysisAvailability[]> {
    const encoded = encodeURIComponent(recordingId);
    const data = await this.getJson<{ analyses?: AnalysisAvailability[] }>(
      `${this.basePath}/outstream/recordings/${encoded}/analyses`,
      { signal }
    );
    return data.analyses ?? [];
  }

  async fetchFrame(recordingId: string, frameIndex: number, signal?: AbortSignal): Promise<{ bytes: ArrayBuffer; metas: FrameResponseMeta[] }> {
    const encoded = encodeURIComponent(recordingId);
    const response = await this.fetchOk(`${this.basePath}/outstream/recordings/${encoded}/frames/${frameIndex}`, { signal });
    const bytes = await response.arrayBuffer();
    return { bytes, metas: [metaFromHeaders(response.headers, frameIndex)] };
  }

  async fetchFrameRange(
    recordingId: string,
    startFrameIndex: number,
    endFrameIndex: number,
    signal?: AbortSignal
  ): Promise<{ bytes: ArrayBuffer; metas: FrameResponseMeta[] }> {
    const encoded = encodeURIComponent(recordingId);
    const params = new URLSearchParams({
      start_frame_index: String(Math.max(0, startFrameIndex)),
      end_frame_index: String(Math.max(startFrameIndex, endFrameIndex)),
      limit: String(Math.max(1, endFrameIndex - startFrameIndex))
    });
    const response = await this.fetchOk(`${this.basePath}/outstream/recordings/${encoded}/frames?${params}`, { signal });
    const bytes = await response.arrayBuffer();
    const metas = parseMetadataHeader(response.headers.get('x-streamlog-frame-metadata'));
    return { bytes, metas };
  }

  async resolveSegmentation(recordingId: string, frameNumber: number, signal?: AbortSignal): Promise<ArrayBuffer | null> {
    const encoded = encodeURIComponent(recordingId);
    const url = `${this.basePath}/outstream/recordings/${encoded}/annotations/segmentation:resolve?frame_number=${frameNumber}`;
    const response = await fetch(url, { signal });
    if (response.status === 404) return null;
    await ensureOk(response);
    return response.arrayBuffer();
  }

  async fetchSegmentationRange(
    recordingId: string,
    startFrameNumber: number,
    endFrameNumber: number,
    signal?: AbortSignal
  ): Promise<ArrayBuffer | null> {
    const encoded = encodeURIComponent(recordingId);
    const params = new URLSearchParams({
      start_frame_number: String(Math.max(0, startFrameNumber)),
      end_frame_number: String(Math.max(startFrameNumber, endFrameNumber))
    });
    const response = await fetch(`${this.basePath}/outstream/recordings/${encoded}/annotations/segmentation?${params}`, { signal });
    if (response.status === 404) return null;
    await ensureOk(response);
    return response.arrayBuffer();
  }

  async fetchAnalysisRecords(
    recordingId: string,
    analysis: string,
    artifact = 'proto',
    signal?: AbortSignal,
    includeSummary = true
  ): Promise<ArrayBuffer | null> {
    const encoded = encodeURIComponent(recordingId);
    const params = new URLSearchParams({ artifact, include_summary: String(includeSummary) });
    const response = await fetch(`${this.basePath}/outstream/recordings/${encoded}/analyses/${analysis}/records?${params}`, { signal });
    if (response.status === 404) return null;
    await ensureOk(response);
    return response.arrayBuffer();
  }

  async fetchAnalysisArtifact(recordingId: string, analysis: string, artifact = 'proto', signal?: AbortSignal): Promise<ArrayBuffer | null> {
    const encoded = encodeURIComponent(recordingId);
    const response = await fetch(`${this.basePath}/outstream/recordings/${encoded}/analyses/${analysis}/artifacts/${artifact}`, { signal });
    if (response.status === 404) return null;
    await ensureOk(response);
    return response.arrayBuffer();
  }

  async fetchSensors(recordingId: string, signal?: AbortSignal): Promise<SensorFrame[]> {
    const encoded = encodeURIComponent(recordingId);
    const data = await this.getJson<{ frames?: SensorFrame[] }>(`${this.basePath}/outstream/recordings/${encoded}/sensors`, { signal });
    return data.frames ?? [];
  }

  async fetchTrajectory(recordingId: string, signal?: AbortSignal): Promise<TrajectoryPoint[]> {
    const encoded = encodeURIComponent(recordingId);
    const data = await this.getJson<{ positions?: TrajectoryPoint[] }>(`${this.basePath}/outstream/recordings/${encoded}/trajectory`, { signal });
    return data.positions ?? [];
  }

  async fetchSensorDataset(recordingId: string, signal?: AbortSignal): Promise<SensorDataset> {
    const [frames, trajectory] = await Promise.all([this.fetchSensors(recordingId, signal), this.fetchTrajectory(recordingId, signal)]);
    return { frames, trajectory };
  }

  async fetchSummary(recordingId: string, signal?: AbortSignal): Promise<ArrayBuffer | null> {
    const encoded = encodeURIComponent(recordingId);
    const response = await fetch(`${this.basePath}/insightgen/recordings/${encoded}/summary`, { signal });
    if (response.status === 404) return null;
    await ensureOk(response);
    return response.arrayBuffer();
  }

  async fetchChat(recordingId: string, sinceTimestampNs = 0, signal?: AbortSignal): Promise<ArrayBuffer | null> {
    const encoded = encodeURIComponent(recordingId);
    const params = new URLSearchParams({ since_timestamp_ns: String(sinceTimestampNs) });
    const response = await fetch(`${this.basePath}/insightgen/recordings/${encoded}/chat?${params}`, { signal });
    if (response.status === 404) return null;
    await ensureOk(response);
    return response.arrayBuffer();
  }

  async sendChat(recordingId: string, message: string, signal?: AbortSignal): Promise<unknown> {
    const encoded = encodeURIComponent(recordingId);
    return this.getJson(`${this.basePath}/insightgen/recordings/${encoded}/chat`, {
      signal,
      headers: { 'content-type': 'application/json' },
      timeoutMs: 120_000
    }, JSON.stringify({ message }));
  }

  async startLivePlayback(signal?: AbortSignal): Promise<void> {
    await this.fetchOk('/api/playback/live', { signal }, undefined, 'POST');
  }

  dashboardWebSocketUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}${this.basePath}/outstream/dashboard/ws`;
  }

  async startImport(file: File, signal?: AbortSignal, onProgress?: (loaded: number, total: number) => void): Promise<unknown> {
    const start = await this.getJson<{ import_id?: string; id?: string }>(
      `${this.basePath}/instream/imports`,
      { signal, headers: { 'content-type': 'application/json' } },
      JSON.stringify({ file_name: file.name, size_bytes: file.size })
    );
    const importId = start.import_id ?? start.id;
    if (!importId) throw new Error('Import session did not return an import_id');
    await uploadWithProgress(`${this.basePath}/instream/imports/${importId}/content`, file, signal, onProgress);
    return this.getJson(`${this.basePath}/instream/imports/${importId}:complete`, { signal }, undefined, 'POST');
  }

  async checkEndpoints(recordingId?: string, activeAnalysis?: string): Promise<EndpointCheck[]> {
    const checks: Array<{ name: string; kind: string; url: string }> = [
      { name: 'Global health', kind: 'HTTP', url: `${this.basePath}/health` },
      { name: 'Outstream health', kind: 'HTTP', url: `${this.basePath}/outstream/health` },
      { name: 'Instream health', kind: 'HTTP', url: `${this.basePath}/instream/health` },
      { name: 'Insightgen health', kind: 'HTTP', url: `${this.basePath}/insightgen/health` },
      { name: 'Analyzers health', kind: 'HTTP', url: `${this.basePath}/analyzers/health` },
      { name: 'Recording library', kind: 'HTTP', url: `${this.basePath}/insightgen/recordings` }
    ];
    if (recordingId) {
      const id = encodeURIComponent(recordingId);
      checks.push(
        { name: 'Current manifest', kind: 'HTTP', url: `${this.basePath}/insightgen/recordings/${id}` },
        { name: 'Frame range', kind: 'HTTP', url: `${this.basePath}/outstream/recordings/${id}/frames?start_frame_index=0&end_frame_index=1&limit=1` },
        { name: 'Segmentation', kind: 'HTTP', url: `${this.basePath}/outstream/recordings/${id}/annotations/segmentation?limit=1` }
      );
      if (activeAnalysis) {
        checks.push({
          name: `Active ${activeAnalysis}`,
          kind: 'HTTP',
          url: `${this.basePath}/outstream/recordings/${id}/analyses/${activeAnalysis}/records?artifact=proto&limit=1`
        });
      }
    }

    return Promise.all(
      checks.map(async (check) => {
        const started = performance.now();
        try {
          const controller = new AbortController();
          const timeout = window.setTimeout(() => controller.abort(), 6000);
          const response = await fetch(check.url, { signal: controller.signal });
          window.clearTimeout(timeout);
          if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
          return {
            ...check,
            status: 'ok' as const,
            latencyMs: Math.round(performance.now() - started),
            detail: response.headers.get('content-type') ?? 'ok'
          };
        } catch (error) {
          return {
            ...check,
            status: 'failed' as const,
            latencyMs: Math.round(performance.now() - started),
            detail: error instanceof Error ? error.message : String(error)
          };
        }
      })
    );
  }

  private async getJson<T>(url: string, options: RequestOptions = {}, body?: BodyInit, method?: string): Promise<T> {
    const response = await this.fetchOk(url, { ...options, headers: options.headers }, body, method);
    return response.json() as Promise<T>;
  }

  private async fetchOk(url: string, options: RequestOptions = {}, body?: BodyInit, method?: string): Promise<Response> {
    const controller = new AbortController();
    const signal = anySignal([controller.signal, options.signal]);
    const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs ?? 30_000);
    try {
      const response = await fetch(url, {
        method: method ?? (body ? 'POST' : 'GET'),
        body,
        headers: options.headers,
        signal
      });
      await ensureOk(response);
      return response;
    } finally {
      window.clearTimeout(timeout);
    }
  }
}

function parseMetadataHeader(value: string | null): FrameResponseMeta[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(normalizeMeta).filter((item): item is FrameResponseMeta => Boolean(item)) : [];
  } catch {
    return [];
  }
}

function metaFromHeaders(headers: Headers, fallbackIndex: number): FrameResponseMeta {
  return {
    recording_id: headers.get('x-streamlog-recording-id') ?? undefined,
    frame_index: numberHeader(headers, 'x-streamlog-frame-index', fallbackIndex),
    frame_number: numberHeader(headers, 'x-streamlog-frame-number', fallbackIndex),
    timestamp_ns: numberHeader(headers, 'x-streamlog-timestamp-ns', 0),
    relative_timestamp_ns: numberHeader(headers, 'x-streamlog-relative-timestamp-ns', 0),
    selector_match_delta_ns: numberHeader(headers, 'x-streamlog-selector-match-delta-ns', 0),
    payload_media_type: 'application/x-protobuf',
    byte_length: numberHeader(headers, 'x-streamlog-byte-length', 0)
  };
}

function normalizeMeta(value: unknown): FrameResponseMeta | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  return {
    recording_id: stringValue(row.recording_id),
    frame_index: numberValue(row.frame_index),
    frame_number: numberValue(row.frame_number),
    timestamp_ns: numberValue(row.timestamp_ns),
    relative_timestamp_ns: numberValue(row.relative_timestamp_ns),
    selector_match_delta_ns: numberValue(row.selector_match_delta_ns),
    payload_media_type: stringValue(row.payload_media_type),
    byte_length: numberValue(row.byte_length)
  };
}

function numberHeader(headers: Headers, key: string, fallback: number): number {
  const value = headers.get(key);
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function finiteOr(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

async function ensureOk(response: Response): Promise<void> {
  if (response.ok) return;
  let detail: unknown = undefined;
  try {
    detail = await response.clone().json();
  } catch {
    try {
      detail = await response.clone().text();
    } catch {
      detail = undefined;
    }
  }
  const message =
    typeof detail === 'object' && detail && 'error' in detail
      ? JSON.stringify((detail as { error: unknown }).error)
      : `${response.status} ${response.statusText}`;
  throw new StreamlogHttpError(response.status, message, detail);
}

function anySignal(signals: Array<AbortSignal | undefined>): AbortSignal {
  const active = signals.filter(Boolean) as AbortSignal[];
  if (active.length === 1) return active[0];
  const controller = new AbortController();
  const abort = () => controller.abort();
  active.forEach((signal) => {
    if (signal.aborted) controller.abort();
    signal.addEventListener('abort', abort, { once: true });
  });
  return controller.signal;
}

function uploadWithProgress(url: string, file: File, signal?: AbortSignal, onProgress?: (loaded: number, total: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('PUT', url);
    request.responseType = 'json';
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded, event.total);
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve();
      else reject(new StreamlogHttpError(request.status, request.statusText, request.response));
    };
    request.onerror = () => reject(new Error('Upload failed'));
    request.onabort = () => reject(new DOMException('Upload aborted', 'AbortError'));
    signal?.addEventListener('abort', () => request.abort(), { once: true });
    request.send(file);
  });
}
