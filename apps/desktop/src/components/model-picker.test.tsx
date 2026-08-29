import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { registry } from '@/contrib/registry'
import type { HermesGateway } from '@/hermes'
import {
  MODEL_PICKER_PROVIDERS_AREA,
  type ModelPickerProviderContribution
} from '@/lib/model-picker-contributions'

import { ModelPickerDialog } from './model-picker'

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  globalThis.ResizeObserver = class ResizeObserver {
    disconnect() {}
    observe() {}
    unobserve() {}
  }
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('model picker provider contributions', () => {
  it('renders before backend rows and selects through the picker canonical path', async () => {
    const onOpenChange = vi.fn()
    const onSelect = vi.fn()

    const gateway = {
      request: vi.fn().mockResolvedValue({
        model: 'backend-current',
        provider: 'core',
        providers: [{ models: ['backend-current'], name: 'Core provider', slug: 'core' }]
      })
    } as unknown as HermesGateway

    const data: ModelPickerProviderContribution = {
      keywords: ['hosted', 'remote'],
      render: ({ close, currentModel, currentProvider, scopeKey, search, select }) => (
        <div data-testid="contributed-provider">
          <span>{`${search}|${currentProvider}|${currentModel}|${scopeKey}`}</span>
          <button onClick={() => select({ model: 'hosted-model', provider: 'hosted' })}>Choose hosted</button>
          <button onClick={() => select({ model: ' ', provider: '' })}>Choose malformed</button>
          <button onClick={close}>Close hosted</button>
        </div>
      )
    }

    const dispose = registry.register({
      area: MODEL_PICKER_PROVIDERS_AREA,
      data,
      id: 'hosted-provider',
      source: 'plugin:test'
    })

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <ModelPickerDialog
          currentModel=""
          currentProvider=""
          gw={gateway}
          onOpenChange={onOpenChange}
          onSelect={onSelect}
          open
          profile="work"
        />
      </QueryClientProvider>
    )

    const contribution = await screen.findByTestId('contributed-provider')
    const backendRow = await screen.findByText('backend-current')

    expect(contribution.compareDocumentPosition(backendRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('|core|backend-current|work:global')).toBeTruthy()

    fireEvent.change(screen.getByPlaceholderText('Filter providers and models...'), { target: { value: 'hosted' } })
    expect(screen.getByText('hosted|core|backend-current|work:global')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Choose malformed' }))
    expect(onSelect).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Choose hosted' }))
    expect(onSelect).toHaveBeenCalledWith({ model: 'hosted-model', provider: 'hosted' })
    expect(onOpenChange).toHaveBeenCalledWith(false)

    onOpenChange.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Close hosted' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)

    dispose()
  })
})
