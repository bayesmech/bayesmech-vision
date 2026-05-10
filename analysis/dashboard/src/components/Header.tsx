import { useState } from 'react'
import { useDashboard } from '../context/DashboardContext'
import EndpointHealthDialog from './EndpointHealthDialog'
import UploadButton from './UploadButton'

const Header = () => {
  const { connectionStatus } = useDashboard()
  const [showEndpointHealth, setShowEndpointHealth] = useState(false)

  const isConnected = connectionStatus === 'Connected'

  return (
    <header className="header">
      <div className="header-content">
        <div className="logo">
          <img src="/logo.png" alt="Logo" style={{ height: 32 }} />
          <h1>Bayesmech Vision</h1>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <UploadButton />
          <button
            className={`status-badge${isConnected ? '' : ' disconnected'}`}
            type="button"
            onClick={() => setShowEndpointHealth(true)}
            title="Show Streamlog endpoint health"
          >
            <span className="status-dot" />
            {connectionStatus}
          </button>
        </div>
      </div>
      {showEndpointHealth && (
        <EndpointHealthDialog
          connectionStatus={connectionStatus}
          onClose={() => setShowEndpointHealth(false)}
        />
      )}
    </header>
  )
}

export default Header
