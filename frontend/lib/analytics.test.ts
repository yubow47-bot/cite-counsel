import { describe, it, expect, vi, beforeEach } from 'vitest'
import { trackEvent } from './analytics'

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('trackEvent', () => {
  it('calls window.gtag with correct event name and params when gtag is defined', () => {
    const gtag = vi.fn()
    window.gtag = gtag

    trackEvent('citation_generated', { some_param: 'value' })

    expect(gtag).toHaveBeenCalledTimes(1)
    expect(gtag).toHaveBeenCalledWith('event', 'citation_generated', { some_param: 'value' })
  })

  it('calls window.gtag with just event name when no params provided', () => {
    const gtag = vi.fn()
    window.gtag = gtag

    trackEvent('test_event')

    expect(gtag).toHaveBeenCalledWith('event', 'test_event', undefined)
  })

  it('does not throw when window.gtag is undefined', () => {
    delete (window as any).gtag

    expect(() => trackEvent('test_event')).not.toThrow()
  })

  it('does not throw when window is undefined (SSR)', () => {
    const origWindow = globalThis.window
    // @ts-expect-error — simulating SSR environment
    delete globalThis.window

    expect(() => trackEvent('test_event')).not.toThrow()

    globalThis.window = origWindow
  })
})
