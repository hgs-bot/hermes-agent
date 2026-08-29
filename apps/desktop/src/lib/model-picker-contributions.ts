import type { ReactNode } from 'react'

/** Provider-specific rows rendered at the top of the canonical model picker. */
export const MODEL_PICKER_PROVIDERS_AREA = 'modelPicker.providers'

export interface ModelPickerSelection {
  model: string
  provider: string
}

export interface ModelPickerProviderRenderProps {
  /** The picker-owned search text. Contributions decide how it filters their rows. */
  search: string
  /** Picker-resolved current model (live session selection, then backend fallback). */
  currentModel: string
  /** Picker-resolved current provider (live session selection, then backend fallback). */
  currentProvider: string
  /** Opaque profile/session scope for contribution-owned caches. */
  scopeKey: string
  /** Select through the picker's canonical onSelect + close path. */
  select: (selection: ModelPickerSelection) => void
  /** Close the picker without selecting. */
  close: () => void
}

/** Payload of a `modelPicker.providers` data contribution. */
export interface ModelPickerProviderContribution {
  /** Search aliases the provider surface may use when rendering its own rows. */
  keywords: readonly string[]
  render: (props: ModelPickerProviderRenderProps) => ReactNode
}

export function isModelPickerProviderContribution(value: unknown): value is ModelPickerProviderContribution {
  if (!value || typeof value !== 'object') {
    return false
  }

  const candidate = value as Partial<ModelPickerProviderContribution>

  return (
    Array.isArray(candidate.keywords) &&
    candidate.keywords.every(keyword => typeof keyword === 'string') &&
    typeof candidate.render === 'function'
  )
}
