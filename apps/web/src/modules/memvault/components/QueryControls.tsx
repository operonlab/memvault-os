import type { LoadBudget, MemoryQueryOptions, TaskMode, ThinkingMode } from '../types'

function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: Array<{ value: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-[0.16em]" style={{ color: 'var(--subtext1)' }}>
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="rounded-xl border px-3 py-2.5 text-sm"
        style={{
          backgroundColor: 'var(--mantle)',
          borderColor: 'var(--surface0)',
          color: 'var(--text)',
          minHeight: 44,
        }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export default function QueryControls({
  options,
  onChange,
}: {
  options: MemoryQueryOptions
  onChange: (next: Partial<MemoryQueryOptions>) => void
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
      <SelectField<TaskMode>
        label="Task"
        value={options.taskMode}
        options={[
          { value: 'lookup', label: 'Lookup' },
          { value: 'decide', label: 'Decide' },
          { value: 'build', label: 'Build' },
          { value: 'reflect', label: 'Reflect' },
        ]}
        onChange={(value) => onChange({ taskMode: value })}
      />
      <SelectField<ThinkingMode>
        label="Thinking"
        value={options.thinkingMode}
        options={[
          { value: 'auto', label: 'Auto' },
          { value: 'fast', label: 'Fast' },
          { value: 'slow', label: 'Slow' },
        ]}
        onChange={(value) => onChange({ thinkingMode: value })}
      />
      <SelectField<LoadBudget>
        label="Load Budget"
        value={options.loadBudget}
        options={[
          { value: 'light', label: 'Light' },
          { value: 'standard', label: 'Standard' },
          { value: 'deep', label: 'Deep' },
        ]}
        onChange={(value) => onChange({ loadBudget: value })}
      />
    </div>
  )
}
