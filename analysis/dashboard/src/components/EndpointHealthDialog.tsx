import { useCallback, useEffect, useState } from 'react'
import type { ConnectionStatus } from '../types'
import { checkStreamlogEndpointHealth, type StreamlogEndpointHealth } from '../services/streamlog'

type EndpointHealthDialogProps = {
  connectionStatus: ConnectionStatus
  onClose: () => void
}

const EndpointHealthDialog = ({ connectionStatus, onClose }: EndpointHealthDialogProps) => {
  const [checks, setChecks] = useState<StreamlogEndpointHealth[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    setLoading(true)
    setError(null)
    checkStreamlogEndpointHealth()
      .then(setChecks)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to check endpoints')
        setChecks([])
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return (
    <div className="endpoint-health-backdrop" role="presentation" onClick={onClose}>
      <div
        className="endpoint-health-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="endpoint-health-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="endpoint-health-header">
          <div>
            <h2 id="endpoint-health-title">Streamlog Endpoints</h2>
            <p>Dashboard WebSocket state: {connectionStatus}</p>
          </div>
          <button className="endpoint-health-close" type="button" aria-label="Close endpoint health" onClick={onClose}>
            x
          </button>
        </div>

        <div className="endpoint-health-actions">
          <button type="button" onClick={refresh} disabled={loading}>
            {loading ? 'Checking...' : 'Refresh'}
          </button>
        </div>

        {error && <div className="endpoint-health-error">{error}</div>}

        <div className="endpoint-health-list">
          {checks.map((check) => (
            <div className="endpoint-health-row" key={`${check.kind}:${check.name}:${check.url}`}>
              <div className={`endpoint-health-state${check.ok ? ' ok' : ' failed'}`}>
                <span className="endpoint-health-dot" />
                <span>{check.ok ? 'OK' : 'Fail'}</span>
              </div>
              <div className="endpoint-health-main">
                <div className="endpoint-health-name">
                  <span>{check.name}</span>
                  <span className="endpoint-health-kind">{check.kind}</span>
                </div>
                <div className="endpoint-health-url">{check.url}</div>
                <div className="endpoint-health-detail">
                  {check.detail} · {check.latencyMs} ms
                </div>
              </div>
            </div>
          ))}

          {loading && checks.length === 0 && (
            <div className="endpoint-health-empty">Checking Streamlog endpoints...</div>
          )}
        </div>
      </div>
    </div>
  )
}

export default EndpointHealthDialog
