import {
  Button,
  cn,
  GlyphSpinner,
  type ModelPickerProviderRenderProps,
  useMutation,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'

import { localRuntimeApi, localRuntimeKeys, type LocalRuntimeModel, type LocalRuntimeRelease } from './api'

const PROVIDER = 'hermes-local'
const keywords = ['local', 'llama', 'offline', 'private', 'hermes']

function errorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)

  return message.replace(/^\d{3}:\s*/, '')
}

function matches(search: string, model: LocalRuntimeModel, release: LocalRuntimeRelease): boolean {
  const needle = search.trim().toLowerCase()

  if (!needle) {
    return true
  }

  return [model.display_name, model.id, model.license, release.quant, release.backend, ...keywords]
    .join(' ')
    .toLowerCase()
    .includes(needle)
}

export function LocalRuntimePickerProvider(props: ModelPickerProviderRenderProps) {
  const client = useQueryClient()
  const catalogKey = localRuntimeKeys.catalog(props.scopeKey)
  const statusKey = localRuntimeKeys.status(props.scopeKey)
  const catalog = useQuery({ queryFn: localRuntimeApi.catalog, queryKey: catalogKey })

  const status = useQuery({
    queryFn: localRuntimeApi.status,
    queryKey: statusKey,
    refetchInterval: query => (query.state.data?.state === 'starting' ? 1_000 : false)
  })

  const install = useMutation({
    mutationFn: async ({ model, release }: { model: LocalRuntimeModel; release: LocalRuntimeRelease }) => {
      await localRuntimeApi.pull(model, release)

      return localRuntimeApi.start(model, release)
    },
    onSuccess: next => client.setQueryData(statusKey, next)
  })

  const stop = useMutation({
    mutationFn: localRuntimeApi.stop,
    onSuccess: next => client.setQueryData(statusKey, next)
  })

  const queryError = catalog.error ?? status.error
  const actionError = install.error ?? stop.error

  if (catalog.isPending || status.isPending) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-xs text-(--ui-text-tertiary)">
        <GlyphSpinner />
        Checking local runtime…
      </div>
    )
  }

  if (queryError) {
    return (
      <div
        className="mx-2 my-1 rounded-md border border-destructive/30 px-3 py-2 text-xs text-destructive"
        role="alert"
      >
        Local runtime unavailable: {errorMessage(queryError)}
      </div>
    )
  }

  const models = (catalog.data?.models ?? []).flatMap(model => {
    const release = model.releases[0]

    return release && matches(props.search, model, release) ? [{ model, release }] : []
  })

  if (models.length === 0) {
    return null
  }

  const runtime = status.data
  const busy = install.isPending || stop.isPending

  return (
    <section
      aria-label="Local models"
      className="mx-2 my-1 overflow-hidden rounded-lg border border-(--ui-border-subtle)"
    >
      <div className="flex items-center justify-between bg-(--ui-surface-subtle) px-3 py-1.5 text-[0.6875rem] font-medium text-(--ui-text-secondary)">
        <span>Local · Hermes runtime</span>
        <span>{runtime?.state === 'ready' ? 'Running' : runtime?.state === 'error' ? 'Error' : 'On this device'}</span>
      </div>
      {models.map(({ model, release }) => {
        const running = runtime?.state === 'ready' && runtime.model_id === model.id
        const selected = props.currentProvider === PROVIDER && props.currentModel === model.id

        return (
          <div
            className={cn('flex items-center gap-3 px-3 py-2', selected && 'bg-(--ui-accent)/10')}
            key={`${model.id}:${release.quant}`}
          >
            <span
              aria-hidden
              className={cn(
                'size-2 shrink-0 rounded-full bg-(--ui-text-disabled)',
                running && 'bg-emerald-500 shadow-[0_0_6px_rgb(16_185_129_/_0.7)]'
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{model.display_name}</div>
              <div className="flex flex-wrap gap-x-2 text-[0.6875rem] text-(--ui-text-tertiary)">
                <span>{release.quant}</span>
                <span>{(release.runtime_backend ?? release.backend).toUpperCase()}</span>
                {release.tokens_per_second ? <span>{release.tokens_per_second} tok/s</span> : null}
                {release.vram_estimate_gb ? <span>{release.vram_estimate_gb} GB VRAM</span> : null}
                {model.tool_calling ? <span>Tools ✓</span> : null}
              </div>
            </div>
            {running ? (
              <>
                <Button
                  aria-label={`Use ${model.display_name}`}
                  disabled={busy}
                  onClick={() => props.select({ model: model.id, provider: PROVIDER })}
                  size="sm"
                >
                  Use
                </Button>
                <Button
                  aria-label={stop.isPending ? 'Stopping local runtime' : 'Stop local runtime'}
                  disabled={busy}
                  onClick={() => stop.mutate()}
                  size="sm"
                  variant="ghost"
                >
                  {stop.isPending ? 'Stopping…' : 'Stop'}
                </Button>
              </>
            ) : (
              <Button
                aria-label={`Install & start ${model.display_name}`}
                disabled={busy}
                onClick={() => install.mutate({ model, release })}
                size="sm"
              >
                {install.isPending ? 'Installing…' : 'Install & start'}
              </Button>
            )}
          </div>
        )
      })}
      {runtime?.state === 'error' && runtime.error ? (
        <div className="border-t border-destructive/20 px-3 py-2 text-xs text-destructive" role="alert">
          {runtime.error}
        </div>
      ) : null}
      {actionError ? (
        <div className="border-t border-destructive/20 px-3 py-2 text-xs text-destructive" role="alert">
          {errorMessage(actionError)}
        </div>
      ) : null}
    </section>
  )
}
