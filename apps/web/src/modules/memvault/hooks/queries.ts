import { useQuery } from '@tanstack/react-query'
import { kgApi, memvaultApi } from '../api'
import type { BlockFilters, MemoryQueryOptions } from '../types'

const STALE_TIME = 5 * 60 * 1000

export const memvaultKeys = {
  blocks: (page: number, pageSize: number, filters: BlockFilters) =>
    ['memvault', 'blocks', { page, pageSize, blockType: filters.blockType, tag: filters.tag }] as const,
  profile: () => ['memvault', 'profile'] as const,
  search: (query: string, options: Partial<MemoryQueryOptions>) =>
    ['memvault', 'search', query, options] as const,
  kg: {
    triples: (page: number) => ['memvault', 'kg', 'triples', page] as const,
    communities: () => ['memvault', 'kg', 'communities'] as const,
    communityDetail: (id: string) => ['memvault', 'kg', 'community', id] as const,
    summaries: () => ['memvault', 'kg', 'summaries'] as const,
    attitudes: () => ['memvault', 'kg', 'attitudes'] as const,
    attitudeHistory: (id: string) => ['memvault', 'kg', 'attitude-history', id] as const,
    skills: () => ['memvault', 'kg', 'skills'] as const,
    cascade: (query: string) => ['memvault', 'kg', 'cascade', query] as const,
  },
}

export function useBlocks(page: number, pageSize: number, filters: BlockFilters) {
  return useQuery({
    queryKey: memvaultKeys.blocks(page, pageSize, filters),
    queryFn: () =>
      memvaultApi.listBlocks(page, pageSize, {
        tag: filters.tag,
        blockType: filters.blockType,
      }),
    staleTime: STALE_TIME,
  })
}

export function useProfile() {
  return useQuery({
    queryKey: memvaultKeys.profile(),
    queryFn: () => memvaultApi.getProfile(),
    staleTime: STALE_TIME,
  })
}

export function useMemoryQuery(query: string, options: Partial<MemoryQueryOptions>) {
  return useQuery({
    queryKey: memvaultKeys.search(query, options),
    queryFn: () =>
      options.consumer === 'ui'
        ? memvaultApi.inspectMemory(query, options)
        : memvaultApi.queryMemory(query, options),
    enabled: !!query.trim(),
    staleTime: STALE_TIME,
  })
}

export function useTriples(page: number) {
  return useQuery({
    queryKey: memvaultKeys.kg.triples(page),
    queryFn: () => kgApi.listTriples(page, 20),
    staleTime: STALE_TIME,
  })
}

export function useCommunities() {
  return useQuery({
    queryKey: memvaultKeys.kg.communities(),
    queryFn: () => kgApi.listCommunities(),
    staleTime: STALE_TIME,
  })
}

export function useCommunityDetail(id: string | null) {
  return useQuery({
    queryKey: memvaultKeys.kg.communityDetail(id!),
    queryFn: () => kgApi.getCommunity(id!),
    enabled: !!id,
    staleTime: STALE_TIME,
  })
}

export function useSummaries() {
  return useQuery({
    queryKey: memvaultKeys.kg.summaries(),
    queryFn: () => kgApi.listSummaries(),
    staleTime: STALE_TIME,
  })
}

export function useAttitudes() {
  return useQuery({
    queryKey: memvaultKeys.kg.attitudes(),
    queryFn: () => kgApi.listAttitudes(),
    staleTime: STALE_TIME,
  })
}

export function useAttitudeHistory(id: string | null) {
  return useQuery({
    queryKey: memvaultKeys.kg.attitudeHistory(id!),
    queryFn: () => kgApi.attitudeHistory(id!),
    enabled: !!id,
    staleTime: STALE_TIME,
  })
}

export function useSkills() {
  return useQuery({
    queryKey: memvaultKeys.kg.skills(),
    queryFn: () => kgApi.skillProfiles(),
    staleTime: STALE_TIME,
  })
}

export function useCascadeRecall(query: string) {
  return useQuery({
    queryKey: memvaultKeys.kg.cascade(query),
    queryFn: () => kgApi.cascadeRecall(query),
    enabled: !!query.trim(),
    staleTime: STALE_TIME,
  })
}
