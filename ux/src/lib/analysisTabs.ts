import {
  Box,
  Boxes,
  BrainCircuit,
  CircleDot,
  Cloud,
  FileCode2,
  Film,
  Gauge,
  Image,
  Joystick,
  Layers3,
  Map,
  MessageSquare,
  ScanSearch,
  Waves,
  type LucideIcon,
} from 'lucide-react'
import type { WorkspaceTabType } from '../types'

export function iconForAnalysis(key: string): LucideIcon {
  if (key.startsWith('video:')) return Film
  switch (key) {
    case 'control':
      return Joystick
    case 'video':
      return Film
    case 'rgb':
      return Image
    case 'depth':
      return Waves
    case 'sensors':
      return Gauge
    case 'point-cloud':
      return Cloud
    case 'surface-planes':
    case 'planes':
      return Layers3
    case 'segmentation':
      return Boxes
    case 'motioncap':
      return CircleDot
    case 'idoslam':
      return Map
    case 'genspark':
      return BrainCircuit
    case 'chat':
      return MessageSquare
    case 'reconstruction':
      return Box
    case 'worldgen':
      return ScanSearch
    default:
      return FileCode2
  }
}

export function tabTypeForAnalysis(key: string): WorkspaceTabType {
  if (key === 'point-cloud') return 'planes'
  if (key === 'surface-planes') return 'planes'
  if (key === 'video' || key.startsWith('video:') || key === 'rgb' || key === 'depth') return 'video'
  if (key === 'sensors') return 'sensors'
  if (key === 'worldgen') return 'worldgen'
  return 'analysis'
}

export function analysisKeyForTab(type: WorkspaceTabType, analysisKey?: string): string {
  if (analysisKey) return analysisKey
  if (type === 'planes') return 'surface-planes'
  return type
}
