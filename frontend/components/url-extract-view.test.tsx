import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { UrlExtractView } from './url-extract-view'

// Mock the API client
vi.mock('@/lib/citation-api', () => ({
  postExtractUrl: vi.fn(),
}))

import { postExtractUrl } from '@/lib/citation-api'

const mockPostExtractUrl = postExtractUrl as ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Tab 3 — independent per-source buttons', () => {

  it('renders exactly three Generate citation buttons', () => {
    render(<UrlExtractView />)
    const buttons = screen.getAllByRole('button', { name: /generate citation/i })
    expect(buttons).toHaveLength(3)
  })

  it('URL button submits URL only — DOI and ISBN populated but not sent', async () => {
    mockPostExtractUrl.mockResolvedValue({
      ok: true, route: 'url', status: 'done',
      data: { citations: [{ citation: 'Test citation.' }] },
      error: null,
    })

    const user = userEvent.setup()
    render(<UrlExtractView />)

    await user.type(screen.getByLabelText('URL'), 'https://example.com')
    await user.type(screen.getByLabelText('DOI'), '10.1000/xyz')
    await user.type(screen.getByLabelText('ISBN'), '978-0-19-957685-7')

    const urlButton = screen.getByLabelText('URL').closest('div')!
      .querySelector('button')!
    await user.click(urlButton)

    expect(mockPostExtractUrl).toHaveBeenCalledTimes(1)
    const callArgs = mockPostExtractUrl.mock.calls[0][0]
    expect(callArgs.url).toBe('https://example.com')
    expect(callArgs.doi).toBeUndefined()
    expect(callArgs.isbn).toBeUndefined()
  })

  it('DOI button submits DOI only — URL and ISBN populated but not sent', async () => {
    mockPostExtractUrl.mockResolvedValue({
      ok: true, route: 'url', status: 'done',
      data: { citations: [{ citation: 'Test citation.' }] },
      error: null,
    })

    const user = userEvent.setup()
    render(<UrlExtractView />)

    await user.type(screen.getByLabelText('URL'), 'https://example.com')
    await user.type(screen.getByLabelText('DOI'), '10.1000/xyz')
    await user.type(screen.getByLabelText('ISBN'), '978-0-19-957685-7')

    // DOI section button
    const doiSection = screen.getByLabelText('DOI').closest('div')!
    const doiButton = doiSection.querySelector('button')!
    await user.click(doiButton)

    expect(mockPostExtractUrl).toHaveBeenCalledTimes(1)
    const callArgs = mockPostExtractUrl.mock.calls[0][0]
    expect(callArgs.url).toBeUndefined()
    expect(callArgs.doi).toBe('10.1000/xyz')
    expect(callArgs.isbn).toBeUndefined()
  })

  it('ISBN button submits ISBN only — URL and DOI populated but not sent', async () => {
    mockPostExtractUrl.mockResolvedValue({
      ok: true, route: 'url', status: 'done',
      data: { citations: [{ citation: 'Test citation.' }] },
      error: null,
    })

    const user = userEvent.setup()
    render(<UrlExtractView />)

    await user.type(screen.getByLabelText('URL'), 'https://example.com')
    await user.type(screen.getByLabelText('DOI'), '10.1000/xyz')
    await user.type(screen.getByLabelText('ISBN'), '978-0-19-957685-7')

    const isbnSection = screen.getByLabelText('ISBN').closest('div')!
    const isbnButton = isbnSection.querySelector('button')!
    await user.click(isbnButton)

    expect(mockPostExtractUrl).toHaveBeenCalledTimes(1)
    const callArgs = mockPostExtractUrl.mock.calls[0][0]
    expect(callArgs.url).toBeUndefined()
    expect(callArgs.doi).toBeUndefined()
    expect(callArgs.isbn).toBe('978-0-19-957685-7')
  })

  it('URL help text is only in URL section, not DOI or ISBN', () => {
    render(<UrlExtractView />)
    const helpTexts = screen.getAllByText(/some sites block scraping/i)
    expect(helpTexts).toHaveLength(1)
  })

  it('done response renders citation result', async () => {
    mockPostExtractUrl.mockResolvedValue({
      ok: true, route: 'url', status: 'done',
      data: { citations: [{ citation: 'Test citation.' }] },
      error: null,
    })

    const user = userEvent.setup()
    render(<UrlExtractView />)
    await user.type(screen.getByLabelText('URL'), 'https://example.com')
    const urlButton = screen.getByLabelText('URL').closest('div')!.querySelector('button')!
    await user.click(urlButton)

    expect(screen.getByText('Test citation.')).toBeTruthy()
  })

  it('unsupported response renders failure message', async () => {
    mockPostExtractUrl.mockResolvedValue({
      ok: true, route: 'url', status: 'unsupported',
      data: {},
      error: { reason: 'backend reason' },
    })

    const user = userEvent.setup()
    render(<UrlExtractView />)
    await user.type(screen.getByLabelText('URL'), 'https://example.com')
    const urlButton = screen.getByLabelText('URL').closest('div')!.querySelector('button')!
    await user.click(urlButton)

    expect(screen.getByText(/URL extraction failed/i)).toBeTruthy()
  })

  it('error response renders error message', async () => {
    mockPostExtractUrl.mockResolvedValue({
      ok: false, route: 'url', status: 'error',
      data: {},
      error: { reason: 'Something went wrong.' },
    })

    const user = userEvent.setup()
    render(<UrlExtractView />)
    await user.type(screen.getByLabelText('URL'), 'https://example.com')
    const urlButton = screen.getByLabelText('URL').closest('div')!.querySelector('button')!
    await user.click(urlButton)

    expect(screen.getByText('Something went wrong.')).toBeTruthy()
  })
})
