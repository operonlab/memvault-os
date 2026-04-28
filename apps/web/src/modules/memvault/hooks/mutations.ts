import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { MemoryBlockCreate, MemoryBlockUpdate } from '@/types'
import { logMutation } from '@/shared/utils/actionJournal'
import { kgApi, memvaultApi } from '../api'
import { memvaultKeys } from './queries'

export function useCreateBlock() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: MemoryBlockCreate) => memvaultApi.create(data),
    onSuccess: (_, variables) => {
      logMutation('memvault/createBlock', variables)
      queryClient.invalidateQueries({ queryKey: ['memvault', 'blocks'] })
    },
  })
}

export function useUpdateBlock() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: MemoryBlockUpdate }) =>
      memvaultApi.update(id, data),
    onSuccess: (_, variables) => {
      logMutation('memvault/updateBlock', variables)
      queryClient.invalidateQueries({ queryKey: ['memvault', 'blocks'] })
    },
  })
}

export function useDeleteBlock() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => memvaultApi.delete(id),
    onSuccess: (_, variables) => {
      logMutation('memvault/deleteBlock', variables)
      queryClient.invalidateQueries({ queryKey: ['memvault', 'blocks'] })
    },
  })
}

export function useDeleteTriple() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => kgApi.deleteTriple(id),
    onSuccess: (_, variables) => {
      logMutation('memvault/deleteTriple', variables)
      queryClient.invalidateQueries({ queryKey: ['memvault', 'kg', 'triples'] })
    },
  })
}

export function useDeleteAttitude() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => kgApi.deleteAttitude(id),
    onSuccess: (_, variables) => {
      logMutation('memvault/deleteAttitude', variables)
      queryClient.invalidateQueries({ queryKey: ['memvault', 'kg', 'attitudes'] })
      queryClient.invalidateQueries({ queryKey: ['memvault', 'kg', 'attitude-history'] })
    },
  })
}

export function useUpdateAttitude() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: { fact: string; category: string } }) =>
      kgApi.updateAttitude(id, data),
    onSuccess: (_, variables) => {
      logMutation('memvault/updateAttitude', variables)
      queryClient.invalidateQueries({ queryKey: ['memvault', 'kg', 'attitudes'] })
      queryClient.invalidateQueries({ queryKey: ['memvault', 'kg', 'attitude-history'] })
    },
  })
}

export function useRecalculateProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => memvaultApi.recalculateProfile(),
    onSuccess: (data) => {
      logMutation('memvault/recalculateProfile')
      queryClient.setQueryData(memvaultKeys.profile(), data)
    },
  })
}
