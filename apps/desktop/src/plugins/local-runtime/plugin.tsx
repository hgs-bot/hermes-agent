import {
  type HermesPlugin,
  MODEL_PICKER_PROVIDERS_AREA,
  type ModelPickerProviderContribution
} from '@hermes/plugin-sdk'

import { bindLocalRuntimeApi } from './api'
import { LocalRuntimePickerProvider } from './picker-provider'

const plugin: HermesPlugin = {
  id: 'local-runtime',
  name: 'Local Runtime',
  description: 'Install, run, and select Hermes-managed local models from the model picker.',
  register(ctx) {
    bindLocalRuntimeApi(ctx.rest)
    ctx.onDispose(() => bindLocalRuntimeApi(null))
    ctx.register({
      id: 'provider',
      area: MODEL_PICKER_PROVIDERS_AREA,
      data: {
        keywords: ['local', 'llama', 'offline', 'private'],
        render: props => <LocalRuntimePickerProvider {...props} />
      } satisfies ModelPickerProviderContribution
    })
  }
}

export default plugin
