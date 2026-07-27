import { createContext, useContext } from 'react'
import type { DomainTriangulation, MotionCaptureOverlay, SegMask } from '../types'

export type CommandResult = {
  ok: boolean
  message: string
}

export type CommandProgress = {
  message: string
  ok?: boolean
  append?: boolean
}

export type SegmentationCommand =
  | { action: 'show'; raw: string }
  | { action: 'list'; raw: string }
  | { action: 'mask'; raw: string; label: string }
  | { action: 'unmask'; raw: string }
  | { action: 'unknown'; raw: string; subcommand: string }
  | { action: 'invalid'; raw: string; message: string }

export type WorldgenCommand =
  | { action: 'run'; raw: string; startRef: string; endRef: string }
  | { action: 'invalid'; raw: string; message: string }

export type ContextCommand =
  | { action: 'list'; raw: string }
  | { action: 'switch'; raw: string; context: string }

// Overlay/command state shared between the AI chat box, the video command bar,
// and the video renderer. Owned by App, consumed via context by the workspace.
export type OverlayState = {
  // Whether the segmentation overlay is currently enabled.
  segmentation: boolean
  // Optional label whose mask should remain visible while all other pixels dim.
  segmentationMaskLabel: string | null
  // Run a command string (e.g. "/segmentation"). Returns a status for display.
  runCommand: (text: string, onProgress?: (progress: CommandProgress) => void) => Promise<CommandResult>
  // Fetch segmentation masks for a video frame number, or null when no artifact
  // or frame source is available. The dedicated Segmentation tab uses this even
  // when the optional overlay on the regular Video tab is disabled.
  getSegmentation: (frameNumber: number, artifactPath?: string) => Promise<SegMask[] | null>
  // All tracked entity labels in the selected recording's segmentation artifact.
  getSegmentationLabels: (artifactPath?: string) => Promise<string[]>
  // Whether the selected recording has a domain reconstruction capable of
  // projecting its generated table/net rectangles back into the video.
  triangulationAvailable: boolean
  // Table and optional net quadrilaterals synchronized to a video frame.
  getDomainTriangulation: (frameNumber: number, artifactPath?: string) => Promise<DomainTriangulation | null>
  // Heatmap and recent track tails synchronized to a video frame.
  getMotionCapture: (frameNumber: number, artifactPath?: string) => Promise<MotionCaptureOverlay | null>
}

export const OverlayContext = createContext<OverlayState | null>(null)

export function useOverlay(): OverlayState | null {
  return useContext(OverlayContext)
}

// True when a message contains a recognized "/command". Used by the AI chat box
// to intercept commands instead of sending them to the assistant.
export function isCommand(text: string): boolean {
  return parseContextCommand(text) !== null
    || parseSegmentationCommand(text) !== null
    || parseWorldgenCommand(text) !== null
}

export function normalizeSegmentationLabel(label: string): string {
  return label.replace(/_/g, ' ').trim().replace(/\s+/g, ' ').toLowerCase()
}

function parseMaskLabel(input: string): string {
  const trimmed = input.trim()
  const quoted = /^["']([^"']+)["']/.exec(trimmed)
  const rawLabel = quoted?.[1] ?? trimmed
  return rawLabel.replace(/[,.!?;:]+$/g, '').replace(/_/g, ' ').trim()
}

export function parseSegmentationCommand(text: string): SegmentationCommand | null {
  const match = /(^|\s)\/segmentation(?::([a-z-]+))?(?:\s+([^\r\n]*))?/i.exec(text)
  if (!match) return null

  const raw = match[0].trim()
  const subcommand = match[2]?.toLowerCase()
  if (!subcommand) return { action: 'show', raw }
  if (subcommand === 'list') return { action: 'list', raw }
  if (subcommand === 'unmask') return { action: 'unmask', raw }
  if (subcommand === 'mask') {
    const label = parseMaskLabel(match[3] ?? '')
    if (!label) return { action: 'invalid', raw, message: 'Usage: /segmentation:mask label_name' }
    return { action: 'mask', raw, label }
  }
  return { action: 'unknown', raw, subcommand }
}

export function parseWorldgenCommand(text: string): WorldgenCommand | null {
  const commandMatch = /(^|\s)\/worldgen\b/i.exec(text)
  if (!commandMatch) return null

  const commandStart = commandMatch.index + commandMatch[0].search(/\/worldgen/i)
  const afterCommand = text.slice(commandStart)
  const rawLine = afterCommand.split(/\r?\n/)[0].trim()
  const segmentMatch = /@([A-Za-z][A-Za-z0-9]*)\s*-\s*@([A-Za-z][A-Za-z0-9]*)/.exec(rawLine)
  if (!segmentMatch) {
    return {
      action: 'invalid',
      raw: rawLine || '/worldgen',
      message: 'Usage: /worldgen @MarkerA-@MarkerB',
    }
  }
  return {
    action: 'run',
    raw: segmentMatch[0],
    startRef: segmentMatch[1],
    endRef: segmentMatch[2],
  }
}

export function parseContextCommand(text: string): ContextCommand | null {
  const commandMatch = /(^|\s)\/context\b/i.exec(text)
  if (!commandMatch) return null

  const commandStart = commandMatch.index + commandMatch[0].search(/\/context/i)
  const rawLine = text.slice(commandStart).split(/\r?\n/)[0].trim()
  const context = rawLine.slice('/context'.length).trim()
  if (!context) return { action: 'list', raw: rawLine || '/context' }
  return {
    action: 'switch',
    raw: rawLine,
    context: context.replace(/^["']|["']$/g, '').trim(),
  }
}
