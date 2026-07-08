import { createContext, useContext } from 'react'
import type { VisFrame } from '../types'

// Fetch a single decoded frame by index (0 .. frameCount-1) for the currently
// selected recording. Resolves to null when the recording has no frame there or
// no frame source is available. Provided by App, consumed by the video player.
export type FrameGetter = (index: number) => Promise<VisFrame | null>

export const FrameSourceContext = createContext<FrameGetter | null>(null)

export function useFrameSource(): FrameGetter | null {
  return useContext(FrameSourceContext)
}
