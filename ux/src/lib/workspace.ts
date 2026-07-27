import type { LayoutNode, LeafNode, WorkspaceTab, WorkspaceTabRequest, WorkspaceTabType } from '../types'

const tabTitles: Record<WorkspaceTabType, string> = {
  control: 'Control',
  scene: 'Scene 3D',
  'point-cloud': 'Point Cloud',
  planes: 'Surface Estimates',
  video: 'Video',
  sensors: 'Sensor Data',
  analysis: 'Analysis',
  worldgen: 'World Modeling',
}

export function createId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 9)}`
}

export function createTab(
  type: WorkspaceTabType,
  title = tabTitles[type],
  analysisKey?: string,
  worldgenResultId?: string,
  sourcePath?: string,
  contextLabel?: string,
): WorkspaceTab {
  return {
    id: createId('tab'),
    type,
    title,
    contextLabel,
    analysisKey,
    worldgenResultId,
    sourcePath,
  }
}

export function createLeaf(tabs: WorkspaceTab[] = [createTab('video')]): LeafNode {
  return {
    id: createId('leaf'),
    type: 'leaf',
    activeTabId: tabs[0]?.id ?? '',
    tabs,
  }
}

export function createInitialLayout(): LayoutNode {
  return createLeaf([createTab('video')])
}

export function firstLeafId(node: LayoutNode): string {
  if (node.type === 'leaf') return node.id
  return firstLeafId(node.first)
}

// Locate the first tab of a given type anywhere in the layout. Used to keep the
// video tab unique — a second request focuses the existing one instead of adding.
export function findTabByType(node: LayoutNode, type: WorkspaceTabType): { leafId: string; tabId: string } | null {
  if (node.type === 'leaf') {
    const tab = node.tabs.find((item) => item.type === type)
    return tab ? { leafId: node.id, tabId: tab.id } : null
  }
  return findTabByType(node.first, type) ?? findTabByType(node.second, type)
}

export function findTabByAnalysisKey(node: LayoutNode, analysisKey: string): { leafId: string; tabId: string } | null {
  if (node.type === 'leaf') {
    const tab = node.tabs.find((item) => item.analysisKey === analysisKey)
    return tab ? { leafId: node.id, tabId: tab.id } : null
  }
  return findTabByAnalysisKey(node.first, analysisKey) ?? findTabByAnalysisKey(node.second, analysisKey)
}

export function hasTabOfType(node: LayoutNode, type: WorkspaceTabType): boolean {
  return findTabByType(node, type) !== null
}

// New leaves default to Video, but never a second one — Surface Estimates fills
// in once a video tab already exists.
export function defaultTabType(node: LayoutNode): WorkspaceTabType {
  return hasTabOfType(node, 'video') ? 'planes' : 'video'
}

export function visitLeaves(node: LayoutNode, visitor: (leaf: LeafNode) => void): void {
  if (node.type === 'leaf') {
    visitor(node)
    return
  }
  visitLeaves(node.first, visitor)
  visitLeaves(node.second, visitor)
}

export function updateLeaf(node: LayoutNode, leafId: string, updater: (leaf: LeafNode) => LayoutNode): LayoutNode {
  if (node.type === 'leaf') {
    return node.id === leafId ? updater(node) : node
  }
  return {
    ...node,
    first: updateLeaf(node.first, leafId, updater),
    second: updateLeaf(node.second, leafId, updater),
  }
}

export function updateSplitRatio(node: LayoutNode, splitId: string, ratio: number): LayoutNode {
  if (node.type === 'leaf') return node
  if (node.id === splitId) {
    return { ...node, ratio: Math.min(0.82, Math.max(0.18, ratio)) }
  }
  return {
    ...node,
    first: updateSplitRatio(node.first, splitId, ratio),
    second: updateSplitRatio(node.second, splitId, ratio),
  }
}

export function addTabToLeaf(node: LayoutNode, leafId: string, request: WorkspaceTabRequest): LayoutNode {
  return updateLeaf(node, leafId, (leaf) => {
    const tab = createTab(
      request.type,
      request.title,
      request.analysisKey,
      request.worldgenResultId,
      request.sourcePath,
    )
    return {
      ...leaf,
      activeTabId: tab.id,
      tabs: [...leaf.tabs, tab],
    }
  })
}

export function activateTab(node: LayoutNode, leafId: string, tabId: string): LayoutNode {
  return updateLeaf(node, leafId, (leaf) => ({ ...leaf, activeTabId: tabId }))
}

export function refreshTab(
  node: LayoutNode,
  leafId: string,
  tabId: string,
  request: WorkspaceTabRequest,
): LayoutNode {
  return updateLeaf(node, leafId, (leaf) => ({
    ...leaf,
    activeTabId: tabId,
    tabs: leaf.tabs.map((tab) => tab.id === tabId
      ? {
          ...tab,
          type: request.type,
          title: request.title,
          analysisKey: request.analysisKey,
          worldgenResultId: request.worldgenResultId,
          sourcePath: request.sourcePath,
        }
      : tab),
  }))
}

export function closeTab(node: LayoutNode, leafId: string, tabId: string): LayoutNode {
  return updateLeaf(node, leafId, (leaf) => {
    if (leaf.tabs.length <= 1) return leaf
    const nextTabs = leaf.tabs.filter((tab) => tab.id !== tabId)
    const activeTabId = leaf.activeTabId === tabId ? nextTabs[0].id : leaf.activeTabId
    return { ...leaf, tabs: nextTabs, activeTabId }
  })
}

export function splitLeaf(node: LayoutNode, leafId: string, direction: 'row' | 'column'): LayoutNode {
  const newLeafType = defaultTabType(node)
  return updateLeaf(node, leafId, (leaf) => ({
    id: createId('split'),
    type: 'split',
    direction,
    ratio: 0.5,
    first: leaf,
    second: createLeaf([createTab(newLeafType)]),
  }))
}
