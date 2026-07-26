import { createContext, useContext } from 'react'
import type { VisFrame } from '../types'

// Fetch a single decoded frame by index (0 .. frameCount-1) for the currently
// selected recording, or an explicit per-device stream in a control project.
// Resolves to null when the source has no frame there or no reader is available.
export type FrameGetter = (index: number, sourcePath?: string) => Promise<VisFrame | null>

export const FrameSourceContext = createContext<FrameGetter | null>(null)

export function useFrameSource(): FrameGetter | null {
  return useContext(FrameSourceContext)
}
