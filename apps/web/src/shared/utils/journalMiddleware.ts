/**
 * Zustand middleware that records named actions to the ActionJournal.
 *
 * Usage:
 *   create<MyState>()(devtools(withJournal((set) => ({
 *     count: 0,
 *     increment: () => set({ count: 1 }, false, 'counter/increment'),
 *   })), { name: 'counterStore' }))
 *
 * Only actions with a string name (3rd arg to set) are recorded.
 * Anonymous set() calls pass through without journaling.
 */

import type { StateCreator, StoreMutatorIdentifier } from 'zustand'
import { journal } from './actionJournal'

type WithJournal = <
  T,
  Mps extends [StoreMutatorIdentifier, unknown][] = [],
  Mcs extends [StoreMutatorIdentifier, unknown][] = [],
>(
  initializer: StateCreator<T, Mps, Mcs>,
) => StateCreator<T, Mps, Mcs>

type WithJournalImpl = <T>(initializer: StateCreator<T, [], []>) => StateCreator<T, [], []>

const withJournalImpl: WithJournalImpl = (initializer) => (set, get, api) => {
  const journaledSet: typeof set = (partial, replace, ...args: unknown[]) => {
    const name = args[0]
    if (typeof name === 'string') {
      journal.append(
        {
          type: name,
          payload: typeof partial === 'function' ? undefined : partial,
          timestamp: performance.now(),
          source: 'zustand',
        },
        get(),
      )
    }
    ;(set as Function)(partial, replace, ...args)
  }
  return initializer(journaledSet, get, api)
}

export const withJournal = withJournalImpl as unknown as WithJournal
