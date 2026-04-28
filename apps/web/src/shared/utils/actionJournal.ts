/**
 * ActionJournal — append-only action log for frontend state changes.
 *
 * Ported from core/src/shared/journal.py. Records named actions from
 * Zustand stores and TanStack Query mutations into a unified timeline.
 *
 * Algebraic model: S₁ + A = S₂
 * - Forward replay: apply actions to rebuild state
 * - Undo: replay all except last N
 * - Audit: inspect action history
 */

export interface JournalEntry {
  /** Action name, e.g. 'memvault/setFilters' or 'memvault/createBlock' */
  readonly type: string
  /** Action payload (partial state for zustand, variables for mutations) */
  readonly payload?: unknown
  /** High-resolution timestamp via performance.now() */
  readonly timestamp: number
  /** Origin: zustand set() or TanStack Query mutation */
  readonly source: 'zustand' | 'mutation'
  /** Store name (for zustand) or module name (for mutations) */
  readonly store?: string
}

interface Checkpoint {
  /** Index in entries[] right after this checkpoint was taken */
  index: number
  /** Snapshot of state at this point */
  state: unknown
  /** Timestamp */
  timestamp: number
}

class ActionJournal {
  private _entries: JournalEntry[] = []
  private _checkpoints: Checkpoint[] = []
  private _checkpointInterval: number
  private _maxEntries: number

  constructor(checkpointInterval = 50, maxEntries = 1000) {
    this._checkpointInterval = checkpointInterval
    this._maxEntries = maxEntries
  }

  /** Record an action. Optionally snapshot state for checkpoint. */
  append(entry: JournalEntry, state?: unknown): void {
    this._entries.push(entry)

    if (state !== undefined && this._entries.length % this._checkpointInterval === 0) {
      this._checkpoints.push({
        index: this._entries.length,
        state: structuredClone(state),
        timestamp: performance.now(),
      })
    }

    if (this._entries.length > this._maxEntries) {
      this._trim()
    }
  }

  /** Get recent entries. Default: all. Pass `last` for most recent N. */
  getEntries(last?: number): readonly JournalEntry[] {
    if (last === undefined) return this._entries
    return this._entries.slice(-last)
  }

  /** Get entries filtered by type prefix, e.g. 'memvault/' */
  getByModule(prefix: string): readonly JournalEntry[] {
    return this._entries.filter((e) => e.type.startsWith(prefix))
  }

  /** Get entries filtered by source */
  getBySource(source: 'zustand' | 'mutation'): readonly JournalEntry[] {
    return this._entries.filter((e) => e.source === source)
  }

  /** Total recorded entries */
  get size(): number {
    return this._entries.length
  }

  /** Clear all entries and checkpoints */
  clear(): void {
    this._entries = []
    this._checkpoints = []
  }

  /** Export for debugging / bug reports */
  toJSON(): { entries: JournalEntry[]; checkpoints: number; size: number } {
    return {
      entries: this._entries,
      checkpoints: this._checkpoints.length,
      size: this._entries.length,
    }
  }

  private _trim(): void {
    const half = Math.floor(this._maxEntries / 2)
    this._entries = this._entries.slice(-half)
    // Adjust checkpoint indices
    this._checkpoints = this._checkpoints
      .filter((cp) => cp.index > this._maxEntries - half)
      .map((cp) => ({ ...cp, index: cp.index - (this._maxEntries - half) }))
  }
}

/** Global singleton — shared across all stores and mutations */
export const journal = new ActionJournal()

/** Log a TanStack Query mutation to the journal */
export function logMutation(type: string, variables?: unknown): void {
  journal.append({
    type,
    payload: variables,
    timestamp: performance.now(),
    source: 'mutation',
  })
}

// Expose to dev console
if (typeof window !== 'undefined' && import.meta.env?.DEV) {
  ;(window as Record<string, unknown>).__journal = journal
}
