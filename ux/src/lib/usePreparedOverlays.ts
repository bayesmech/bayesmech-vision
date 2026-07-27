import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  MotionCaptureOverlay,
  MotionTrajectoryMode,
  SegMask,
  VisSummary,
} from '../types'

export type PreparedSegmentationMetadata = {
  objectId: number
  label: string
  color: [number, number, number]
}

type LayerEntry<T> = {
  width: number
  height: number
  payload: T
  bitmap: ImageBitmap | null
}

type JobKind = 'segmentation' | 'motion'

type QueueJob = {
  kind: JobKind
  frameNumber: number
  generation: number
}

type PreparedResponse = {
  type: 'prepared'
  kind: JobKind
  jobId: number
  frameNumber: number
  width: number
  height: number
  bitmap: ImageBitmap | null
  metadata?: PreparedSegmentationMetadata[]
  motion?: MotionCaptureOverlay | null
}

type FailedResponse = {
  type: 'failed'
  kind: JobKind
  jobId: number
  frameNumber: number
  message: string
}

type WorkerResponse = PreparedResponse | FailedResponse

type UsePreparedOverlaysOptions = {
  summary: VisSummary | null
  currentFrameNumber: number | null
  currentFrameIndex: number
  segmentationEnabled: boolean
  selectedSegmentationLabel: string | null
  motionEnabled: boolean
  motionTrajectoryMode: MotionTrajectoryMode
  getSegmentation?: (frameNumber: number) => Promise<SegMask[] | null>
  getMotionCapture?: (frameNumber: number) => Promise<MotionCaptureOverlay | null>
}

const MAX_ACTIVE_PREPARATIONS = 3
const SEGMENTATION_LOOKAHEAD_FRAMES = 100
const MOTION_LOOKAHEAD_FRAMES = 24
const LOOKBEHIND_FRAMES = 12
const MAX_SEGMENTATION_LAYERS = 120
const MAX_MOTION_LAYERS = 26

function estimatedFrameNumber(summary: VisSummary, frameIndex: number): number {
  if (summary.frameCount <= 1) return summary.firstFrameNumber
  const clampedIndex = Math.max(0, Math.min(summary.frameCount - 1, frameIndex))
  const progress = clampedIndex / (summary.frameCount - 1)
  return Math.round(
    summary.firstFrameNumber
      + (summary.lastFrameNumber - summary.firstFrameNumber) * progress,
  )
}

function retireBitmap(bitmap: ImageBitmap | null) {
  if (!bitmap) return
  window.setTimeout(() => bitmap.close(), 1500)
}

function trimCache<T>(
  cache: Map<number, LayerEntry<T>>,
  maximum: number,
  currentFrameNumber: number,
) {
  while (cache.size > maximum) {
    const evictedKey = [...cache.keys()].sort((left, right) => {
      const leftBehind = left < currentFrameNumber
      const rightBehind = right < currentFrameNumber
      if (leftBehind !== rightBehind) return leftBehind ? -1 : 1
      return Math.abs(right - currentFrameNumber) - Math.abs(left - currentFrameNumber)
    })[0]
    const evicted = cache.get(evictedKey)
    if (!evicted) return
    retireBitmap(evicted.bitmap)
    cache.delete(evictedKey)
  }
}

function closeCache<T>(cache: Map<number, LayerEntry<T>>) {
  for (const entry of cache.values()) retireBitmap(entry.bitmap)
  cache.clear()
}

export function usePreparedOverlays({
  summary,
  currentFrameNumber,
  currentFrameIndex,
  segmentationEnabled,
  selectedSegmentationLabel,
  motionEnabled,
  motionTrajectoryMode,
  getSegmentation,
  getMotionCapture,
}: UsePreparedOverlaysOptions) {
  const [displayRevision, setDisplayRevision] = useState(0)

  const workerRef = useRef<Worker | null>(null)
  const generationRef = useRef(0)
  const queueRef = useRef<QueueJob[]>([])
  const inFlightRef = useRef(new Set<string>())
  const activePreparationsRef = useRef(0)
  const nextJobIdRef = useRef(1)
  const pendingWorkerJobsRef = useRef(new Map<number, QueueJob>())
  const segmentationCacheRef = useRef(
    new Map<number, LayerEntry<PreparedSegmentationMetadata[]>>(),
  )
  const motionCacheRef = useRef(
    new Map<number, LayerEntry<MotionCaptureOverlay | null>>(),
  )
  const getSegmentationRef = useRef(getSegmentation)
  const getMotionCaptureRef = useRef(getMotionCapture)
  const selectedLabelRef = useRef(selectedSegmentationLabel)
  const currentFrameNumberRef = useRef(currentFrameNumber)
  const pumpRef = useRef<() => void>(() => {})
  const revisionTimerRef = useRef<number | null>(null)

  currentFrameNumberRef.current = currentFrameNumber

  useEffect(() => {
    getSegmentationRef.current = getSegmentation
  }, [getSegmentation])

  useEffect(() => {
    getMotionCaptureRef.current = getMotionCapture
  }, [getMotionCapture])

  useEffect(() => {
    selectedLabelRef.current = selectedSegmentationLabel
  }, [selectedSegmentationLabel])

  const flightKeyFor = (job: QueueJob) =>
    `${job.generation}:${job.kind}:${job.frameNumber}`

  const completeJob = (job: QueueJob) => {
    activePreparationsRef.current = Math.max(0, activePreparationsRef.current - 1)
    inFlightRef.current.delete(flightKeyFor(job))
    pumpRef.current()
  }

  const scheduleDisplayRevision = (frameNumber?: number) => {
    if (revisionTimerRef.current !== null) {
      window.clearTimeout(revisionTimerRef.current)
    }
    revisionTimerRef.current = window.setTimeout(() => {
      revisionTimerRef.current = null
      if (
        frameNumber !== undefined
        && currentFrameNumberRef.current !== frameNumber
      ) return
      setDisplayRevision((current) => current + 1)
    }, 120)
  }

  useEffect(() => {
    const worker = new Worker(
      new URL('./overlayPrepare.worker.ts', import.meta.url),
      { type: 'module' },
    )
    workerRef.current = worker
    worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
      const response = event.data
      const job = pendingWorkerJobsRef.current.get(response.jobId)
      if (!job) {
        if (response.type === 'prepared') retireBitmap(response.bitmap)
        return
      }
      pendingWorkerJobsRef.current.delete(response.jobId)
      if (response.type === 'prepared' && job.generation === generationRef.current) {
        if (response.kind === 'segmentation') {
          segmentationCacheRef.current.set(response.frameNumber, {
            width: response.width,
            height: response.height,
            payload: response.metadata ?? [],
            bitmap: response.bitmap,
          })
          trimCache(
            segmentationCacheRef.current,
            MAX_SEGMENTATION_LAYERS,
            currentFrameNumberRef.current ?? response.frameNumber,
          )
        } else {
          motionCacheRef.current.set(response.frameNumber, {
            width: response.width,
            height: response.height,
            payload: response.motion ?? null,
            bitmap: response.bitmap,
          })
          trimCache(
            motionCacheRef.current,
            MAX_MOTION_LAYERS,
            currentFrameNumberRef.current ?? response.frameNumber,
          )
        }
      } else if (response.type === 'prepared') {
        retireBitmap(response.bitmap)
      }
      completeJob(job)
      if (
        response.type === 'prepared'
        && response.frameNumber === currentFrameNumberRef.current
      ) {
        scheduleDisplayRevision(response.frameNumber)
      } else if (
        activePreparationsRef.current === 0
        && queueRef.current.length === 0
      ) {
        scheduleDisplayRevision()
      }
    }
    worker.onerror = () => {
      for (const job of pendingWorkerJobsRef.current.values()) completeJob(job)
      pendingWorkerJobsRef.current.clear()
    }
    return () => {
      worker.terminate()
      workerRef.current = null
      pendingWorkerJobsRef.current.clear()
    }
  }, [])

  pumpRef.current = () => {
    const worker = workerRef.current
    if (!worker) return
    while (
      activePreparationsRef.current < MAX_ACTIVE_PREPARATIONS
      && queueRef.current.length > 0
    ) {
      const job = queueRef.current.shift()
      if (!job || job.generation !== generationRef.current) continue
      const cache = job.kind === 'segmentation'
        ? segmentationCacheRef.current
        : motionCacheRef.current
      const flightKey = flightKeyFor(job)
      if (cache.has(job.frameNumber) || inFlightRef.current.has(flightKey)) continue
      const getter = job.kind === 'segmentation'
        ? getSegmentationRef.current
        : getMotionCaptureRef.current
      if (!getter) continue

      activePreparationsRef.current += 1
      inFlightRef.current.add(flightKey)
      const jobId = nextJobIdRef.current
      nextJobIdRef.current += 1
      pendingWorkerJobsRef.current.set(jobId, job)
      if (job.kind === 'segmentation') {
        void getSegmentationRef.current!(job.frameNumber)
          .then((masks) => {
            if (job.generation !== generationRef.current) {
              pendingWorkerJobsRef.current.delete(jobId)
              completeJob(job)
              return
            }
            worker.postMessage({
              type: 'segmentation',
              jobId,
              frameNumber: job.frameNumber,
              masks: masks ?? [],
              selectedLabel: selectedLabelRef.current,
            })
          })
          .catch(() => {
            pendingWorkerJobsRef.current.delete(jobId)
            completeJob(job)
          })
      } else {
        void getMotionCaptureRef.current!(job.frameNumber)
          .then((motion) => {
            if (job.generation !== generationRef.current) {
              pendingWorkerJobsRef.current.delete(jobId)
              completeJob(job)
              return
            }
            worker.postMessage({
              type: 'motion',
              jobId,
              frameNumber: job.frameNumber,
              motion,
              trajectoryMode: motionTrajectoryMode,
            })
          })
          .catch(() => {
            pendingWorkerJobsRef.current.delete(jobId)
            completeJob(job)
          })
      }
    }
  }

  useEffect(() => {
    generationRef.current += 1
    queueRef.current = []
    inFlightRef.current.clear()
    activePreparationsRef.current = 0
    pendingWorkerJobsRef.current.clear()
    closeCache(segmentationCacheRef.current)
    closeCache(motionCacheRef.current)
    setDisplayRevision((current) => current + 1)
  }, [
    summary?.path,
    segmentationEnabled,
    selectedSegmentationLabel,
    motionEnabled,
    motionTrajectoryMode,
  ])

  const configurationKey = [
    summary?.path ?? '',
    segmentationEnabled ? 'segmentation' : '',
    selectedSegmentationLabel ?? '',
    motionEnabled ? motionTrajectoryMode : '',
  ].join('|')

  const targetJobs = useMemo(() => {
    if (!summary) return []
    const jobs: QueueJob[] = []
    const seen = new Set<string>()
    const add = (kind: JobKind, frameIndex: number, exactFrameNumber?: number) => {
      if (frameIndex < 0 || frameIndex >= summary.frameCount) return
      const frameNumber = exactFrameNumber
        ?? estimatedFrameNumber(summary, frameIndex)
      const key = `${kind}:${frameNumber}`
      if (seen.has(key)) return
      seen.add(key)
      jobs.push({ kind, frameNumber, generation: 0 })
    }
    if (segmentationEnabled && getSegmentationRef.current) {
      if (currentFrameNumber !== null) add('segmentation', currentFrameIndex, currentFrameNumber)
      for (let offset = 0; offset <= SEGMENTATION_LOOKAHEAD_FRAMES; offset += 1) {
        add('segmentation', currentFrameIndex + offset)
      }
      for (let offset = 1; offset <= LOOKBEHIND_FRAMES; offset += 1) {
        add('segmentation', currentFrameIndex - offset)
      }
    }
    if (motionEnabled && getMotionCaptureRef.current) {
      if (currentFrameNumber !== null) add('motion', currentFrameIndex, currentFrameNumber)
      for (let offset = 0; offset <= MOTION_LOOKAHEAD_FRAMES; offset += 1) {
        add('motion', currentFrameIndex + offset)
      }
      for (let offset = 1; offset <= LOOKBEHIND_FRAMES; offset += 1) {
        add('motion', currentFrameIndex - offset)
      }
    }
    return [
      ...jobs
        .filter((job) => job.kind === 'motion')
        .slice(0, MAX_MOTION_LAYERS),
      ...jobs
        .filter((job) => job.kind === 'segmentation')
        .slice(0, MAX_SEGMENTATION_LAYERS),
    ]
  }, [
    currentFrameIndex,
    currentFrameNumber,
    motionEnabled,
    segmentationEnabled,
    summary,
    configurationKey,
  ])

  useEffect(() => {
    const generation = generationRef.current
    const desired = new Set(targetJobs.map((job) => `${job.kind}:${job.frameNumber}`))
    queueRef.current = targetJobs
      .map((job) => ({ ...job, generation }))
      .filter((job) => {
        const cache = job.kind === 'segmentation'
          ? segmentationCacheRef.current
          : motionCacheRef.current
        const desiredKey = `${job.kind}:${job.frameNumber}`
        return desired.has(desiredKey)
          && !cache.has(job.frameNumber)
          && !inFlightRef.current.has(flightKeyFor(job))
      })
    pumpRef.current()
  }, [targetJobs])

  useEffect(() => () => {
    generationRef.current += 1
    if (revisionTimerRef.current !== null) {
      window.clearTimeout(revisionTimerRef.current)
      revisionTimerRef.current = null
    }
    closeCache(segmentationCacheRef.current)
    closeCache(motionCacheRef.current)
  }, [])

  const preparedCount = targetJobs.reduce((count, job) => {
    const cache = job.kind === 'segmentation'
      ? segmentationCacheRef.current
      : motionCacheRef.current
    return count + (cache.has(job.frameNumber) ? 1 : 0)
  }, 0)

  // Worker responses advance this revision. The actual layer state remains in
  // the bounded caches, so a seek or mode switch only selects another cached
  // bitmap instead of triggering a second chain of React state updates.
  void displayRevision
  const segmentationEntry = (
    segmentationEnabled && currentFrameNumber !== null
      ? segmentationCacheRef.current.get(currentFrameNumber)
      : null
  )
  const motionEntry = (
    motionEnabled && currentFrameNumber !== null
      ? motionCacheRef.current.get(currentFrameNumber)
      : null
  )

  return {
    segmentationLayer: segmentationEntry?.bitmap ?? null,
    segmentationMetadata: segmentationEntry?.payload ?? [],
    segmentationLoading: (
      segmentationEnabled && currentFrameNumber !== null && !segmentationEntry
    ),
    motionLayer: motionEntry?.bitmap ?? null,
    motionCapture: motionEntry?.payload ?? null,
    motionLoading: motionEnabled && currentFrameNumber !== null && !motionEntry,
    preparedCount,
    preparationTarget: targetJobs.length,
  }
}
