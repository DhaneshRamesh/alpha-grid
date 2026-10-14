import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from './App'

vi.mock('./stream', () => ({ useEngineStream: () => ({ snapshot: null, connection: 'DISCONNECTED', messageLagMs: null, reconnects: 0 }) }))

describe('cockpit without a backend', () => {
  it('shows disconnected and unknown values honestly', () => {
    render(<App />)
    expect(screen.getByText('UI DISCONNECTED')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Command center.' })).toBeTruthy()
    expect(screen.getAllByText('N/A').length).toBeGreaterThan(2)
    expect(screen.queryByText(/profit/i)).toBeNull()
  })
})
