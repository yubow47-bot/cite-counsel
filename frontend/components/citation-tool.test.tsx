import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CitationTool } from './citation-tool'

// Mock the API client
vi.mock('@/lib/citation-api', () => ({
  postCitation: vi.fn(),
}))

// Mock analytics
vi.mock('@/lib/analytics', () => ({
  trackEvent: vi.fn(),
}))

import { postCitation } from '@/lib/citation-api'
import { trackEvent } from '@/lib/analytics'

const mockPostCitation = postCitation as ReturnType<typeof vi.fn>
const mockTrackEvent = trackEvent as ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CitationTool — analytics tracking', () => {
  it('calls trackEvent("citation_generated") when done envelope has non-empty citations', async () => {
    mockPostCitation.mockResolvedValue({
      ok: true, route: 'auto', status: 'done',
      data: { citations: [{ citation: 'R v Jordan, 2016 SCC 27.' }] },
      error: null,
    })

    const user = userEvent.setup()
    render(<CitationTool />)

    const textarea = screen.getByRole('textbox', { name: /enter the source/i })
    await user.type(textarea, 'r v jordan')
    await user.click(screen.getByRole('button', { name: /generate citation/i }))

    expect(mockTrackEvent).toHaveBeenCalledTimes(1)
    expect(mockTrackEvent).toHaveBeenCalledWith('citation_generated')
  })

  it('does NOT call trackEvent when done envelope has empty citations', async () => {
    mockPostCitation.mockResolvedValue({
      ok: true, route: 'auto', status: 'done',
      data: { citations: [] },
      error: null,
    })

    const user = userEvent.setup()
    render(<CitationTool />)

    const textarea = screen.getByRole('textbox', { name: /enter the source/i })
    await user.type(textarea, 'something that returns nothing')
    await user.click(screen.getByRole('button', { name: /generate citation/i }))

    expect(mockTrackEvent).not.toHaveBeenCalled()
  })
})
