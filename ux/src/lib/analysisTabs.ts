import {
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
  Triangle,
  Waves,
  type LucideIcon,
} from 'lucide-react'
import type { WorkspaceTabType } from '../types'

export function baseAnalysisKey(key: string): string {
  return key.split(':')[0]
}

export function iconForAnalysis(key: string): LucideIcon {
  switch (baseAnalysisKey(key)) {
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
    case 'worldgen':
      return ScanSearch
    case 'pongtown':
    case 'snookestown':
      return Triangle
    default:
      return FileCode2
  }
}

export function tabTypeForAnalysis(key: string): WorkspaceTabType {
  const baseKey = baseAnalysisKey(key)
  if (baseKey === 'point-cloud') return 'planes'
  if (baseKey === 'surface-planes') return 'planes'
  if (baseKey === 'video' || baseKey === 'rgb' || baseKey === 'depth') return 'video'
  if (baseKey === 'sensors') return 'sensors'
  if (baseKey === 'worldgen') return 'worldgen'
  return 'analysis'
}

export function analysisKeyForTab(type: WorkspaceTabType, analysisKey?: string): string {
  if (analysisKey) return analysisKey
  if (type === 'planes') return 'surface-planes'
  return type
}
