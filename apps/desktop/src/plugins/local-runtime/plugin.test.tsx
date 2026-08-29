import { MODEL_PICKER_PROVIDERS_AREA, type ModelPickerProviderRenderProps } from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bindLocalRuntimeApi, type LocalRuntimeRest } from './api'
import { LocalRuntimePickerProvider } from './picker-provider'
import plugin from './plugin'

const catalog = {
  schema: 'hermes-local-manifest@1',
  models: [
    {
      id: 'qwen-local',
      display_name: 'Qwen Local',
      license: 'Apache-2.0',
      recommended: true,
      tool_calling: true,
      releases: [
        {
          backend: 'cuda',
          context_length: 16384,
          quant: 'Q4_K_M',
          runtime_backend: 'vulkan',
          size_bytes: 4_000_000_000,
          tokens_per_second: 57.4,
          vram_estimate_gb: 4.8
        }
      ]
    }
  ]
}

function renderProvider(rest: ReturnType<typeof vi.fn>, overrides: Partial<ModelPickerProviderRenderProps> = {}) {
  bindLocalRuntimeApi(rest as LocalRuntimeRest)

  const props: ModelPickerProviderRenderProps = {
    close: vi.fn(),
    currentModel: '',
    currentProvider: '',
    scopeKey: 'test:global',
    search: '',
    select: vi.fn(),
    ...overrides
  }

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={client}>
      <LocalRuntimePickerProvider {...props} />
    </QueryClientProvider>
  )

  return props
}

afterEach(() => {
  cleanup()
  bindLocalRuntimeApi(null)
  vi.clearAllMocks()
})

describe('local runtime bundled plugin', () => {
  it('registers in the canonical model picker provider area', () => {
    const register = vi.fn()

    plugin.register({
      onDispose: vi.fn(),
      register,
      rest: vi.fn()
    } as never)

    expect(register).toHaveBeenCalledWith(
      expect.objectContaining({
        area: MODEL_PICKER_PROVIDERS_AREA,
        data: expect.objectContaining({ keywords: expect.arrayContaining(['local', 'llama']) }),
        id: 'provider'
      })
    )
  })

  it('queries backend catalog and status, then pulls and starts before selecting canonically', async () => {
    let state = 'stopped'

    const rest = vi.fn(async (path: string) => {
      if (path === '/catalog') {
        return catalog
      }

      if (path === '/status') {
        return { state }
      }

      if (path === '/pull') {
        return { files: ['model.gguf'], runtime: 'llama-server', state: 'ready' }
      }

      if (path === '/start') {
        state = 'ready'

        return {
          backend: 'cuda',
          base_url: 'http://127.0.0.1:11435/v1',
          model_id: 'qwen-local',
          port: 11435,
          state
        }
      }

      throw new Error(`unexpected ${path}`)
    })

    const props = renderProvider(rest)

    expect(await screen.findByText('Qwen Local')).toBeTruthy()
    expect(screen.getByText(/57.4 tok\/s/)).toBeTruthy()
    expect(rest).toHaveBeenCalledWith('/catalog')
    expect(rest).toHaveBeenCalledWith('/status')

    fireEvent.click(screen.getByRole('button', { name: 'Install & start Qwen Local' }))

    await screen.findByRole('button', { name: 'Use Qwen Local' })
    expect(rest).toHaveBeenCalledWith('/pull', {
      body: { backend: 'vulkan', model_id: 'qwen-local', quant: 'Q4_K_M' },
      method: 'POST',
      timeoutMs: 1_800_000
    })
    expect(rest).toHaveBeenCalledWith('/start', {
      body: { backend: 'vulkan', model_id: 'qwen-local', port: 11435 },
      method: 'POST',
      timeoutMs: 1_800_000
    })

    fireEvent.click(screen.getByRole('button', { name: 'Use Qwen Local' }))
    expect(props.select).toHaveBeenCalledWith({ model: 'qwen-local', provider: 'hermes-local' })
  })

  it('stops through the backend without claiming success early', async () => {
    let resolveStop!: (value: unknown) => void

    const rest = vi.fn((path: string) => {
      if (path === '/catalog') {
        return Promise.resolve(catalog)
      }

      if (path === '/status') {
        return Promise.resolve({ backend: 'cuda', model_id: 'qwen-local', state: 'ready' })
      }

      if (path === '/stop') {
        return new Promise(resolve => (resolveStop = resolve))
      }

      return Promise.reject(new Error(`unexpected ${path}`))
    })

    renderProvider(rest)

    fireEvent.click(await screen.findByRole('button', { name: 'Stop local runtime' }))
    const stopping = await screen.findByRole('button', { name: 'Stopping local runtime' })
    expect((stopping as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: 'Install & start Qwen Local' })).toBeNull()

    resolveStop({ state: 'stopped' })
    expect(await screen.findByRole('button', { name: 'Install & start Qwen Local' })).toBeTruthy()
  })

  it('shows backend errors and keeps the action retryable', async () => {
    const rest = vi.fn(async (path: string) => {
      if (path === '/catalog') {
        return catalog
      }

      if (path === '/status') {
        return { state: 'stopped' }
      }

      if (path === '/pull') {
        throw new Error('409: checksum mismatch')
      }

      throw new Error(`unexpected ${path}`)
    })

    renderProvider(rest)

    fireEvent.click(await screen.findByRole('button', { name: 'Install & start Qwen Local' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('checksum mismatch')
    expect((screen.getByRole('button', { name: 'Install & start Qwen Local' }) as HTMLButtonElement).disabled).toBe(
      false
    )
    await waitFor(() => expect(rest).not.toHaveBeenCalledWith('/start', expect.anything()))
  })

  it('does not render when picker search does not match local metadata', async () => {
    const rest = vi.fn(async (path: string) => (path === '/catalog' ? catalog : { state: 'stopped' }))
    renderProvider(rest, { search: 'anthropic' })

    await waitFor(() => expect(rest).toHaveBeenCalledWith('/catalog'))
    expect(screen.queryByText('Qwen Local')).toBeNull()
  })
})
