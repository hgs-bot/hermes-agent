import type { PluginRestOptions } from '@hermes/plugin-sdk'

export interface LocalRuntimeRelease {
  backend: 'cpu' | 'cuda' | 'vulkan'
  runtime_backend?: 'cpu' | 'cuda' | 'vulkan'
  context_length: number
  quant: string
  size_bytes: number
  tokens_per_second?: number
  vram_estimate_gb?: number
}

export interface LocalRuntimeModel {
  id: string
  display_name: string
  license: string
  recommended: boolean
  tool_calling: boolean
  releases: LocalRuntimeRelease[]
}

export interface LocalRuntimeCatalog {
  schema: string
  models: LocalRuntimeModel[]
}

export interface LocalRuntimeStatus {
  state: 'error' | 'ready' | 'starting' | 'stopped'
  pid?: number | null
  port?: number | null
  model_id?: string | null
  backend?: string | null
  base_url?: string | null
  error?: string | null
}

export type LocalRuntimeRest = <T>(path: string, options?: PluginRestOptions) => Promise<T>

let rest: LocalRuntimeRest | null = null

export function bindLocalRuntimeApi(next: LocalRuntimeRest | null): void {
  rest = next
}

function request<T>(path: string, options?: PluginRestOptions): Promise<T> {
  if (!rest) {
    return Promise.reject(new Error('Local runtime backend unavailable'))
  }

  return options ? rest<T>(path, options) : rest<T>(path)
}

export const localRuntimeKeys = {
  catalog: (scopeKey: string) => ['plugin', 'local-runtime', scopeKey, 'catalog'] as const,
  status: (scopeKey: string) => ['plugin', 'local-runtime', scopeKey, 'status'] as const
}

export const localRuntimeApi = {
  catalog: () => request<LocalRuntimeCatalog>('/catalog'),
  status: () => request<LocalRuntimeStatus>('/status'),
  pull: (model: LocalRuntimeModel, release: LocalRuntimeRelease) =>
    request('/pull', {
      body: { backend: release.runtime_backend ?? release.backend, model_id: model.id, quant: release.quant },
      method: 'POST',
      timeoutMs: 1_800_000
    }),
  start: (model: LocalRuntimeModel, release: LocalRuntimeRelease) =>
    request<LocalRuntimeStatus>('/start', {
      body: { backend: release.runtime_backend ?? release.backend, model_id: model.id, port: 11435 },
      method: 'POST',
      timeoutMs: 1_800_000
    }),
  stop: () => request<LocalRuntimeStatus>('/stop', { method: 'POST' })
}
