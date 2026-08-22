import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CitationCard } from './citation-card'

// Mock API and clipboard — never make real network calls in tests
vi.mock('@/lib/citation-api', () => ({
  postFeedback: vi.fn(() => Promise.resolve()),
}))

vi.mock('@/lib/clipboard', () => ({
  copyCitation: vi.fn(() => Promise.resolve()),
}))

import { copyCitation } from '@/lib/clipboard'
import { postFeedback } from '@/lib/citation-api'
const mockCopy = copyCitation as ReturnType<typeof vi.fn>
const mockPostFeedback = postFeedback as ReturnType<typeof vi.fn>

const BASE_CITATION = {
  citation: "R v Jordan, 2016 SCC 27.",
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CitationCard — render states', () => {

  it('renders the citation text and action buttons', () => {
    render(<CitationCard item={BASE_CITATION} sourceInput="r v jordan" />)

    expect(screen.getByText(/R v Jordan, 2016 SCC 27\./)).toBeTruthy()
    expect(screen.getByRole('button', { name: /copy citation/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /mark as accurate/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /mark as incorrect/i })).toBeTruthy()
  })

  it('shows Add pinpoint link by default (not open)', () => {
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    expect(screen.getByText(/add pinpoint/i)).toBeTruthy()
    // Input should not be visible yet
    expect(screen.queryByRole('textbox', { name: /pinpoint reference/i })).toBeNull()
  })

  it('shows source_case when provided', () => {
    render(<CitationCard item={{ ...BASE_CITATION, source_case: "R v King" }} sourceInput="" />)

    expect(screen.getByText(/source case/i)).toBeTruthy()
    expect(screen.getByText(/R v King/)).toBeTruthy()
  })

  it('shows manually-entered badge when verified is false', () => {
    render(<CitationCard item={{ ...BASE_CITATION, verified: false }} sourceInput="" />)

    expect(screen.getByText(/manually entered/i)).toBeTruthy()
    expect(screen.getByText(/not database-verified/i)).toBeTruthy()
  })
})

describe('CitationCard — pinpoint add / edit / remove', () => {

  it('opens pinpoint text input on clicking Add pinpoint', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))

    expect(screen.getByRole('textbox', { name: /pinpoint reference/i })).toBeTruthy()
  })

  it('updates the displayed citation when pinpoint is typed', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i })
    await user.type(input, "at para 47")

    // The display should now show "R v Jordan, 2016 SCC 27 at para 47."
    expect(screen.getByText(/R v Jordan, 2016 SCC 27 at para 47\./)).toBeTruthy()
  })

  it('removes pinpoint and restores original citation when X is clicked', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i })
    await user.type(input, "at para 47")

    // Verify concatenated display
    expect(screen.getByText(/at para 47/)).toBeTruthy()

    // Click X to remove
    await user.click(screen.getByRole('button', { name: /remove pinpoint/i }))

    // Pinpoint input should be gone and original text restored
    expect(screen.queryByRole('textbox', { name: /pinpoint reference/i })).toBeNull()
    expect(screen.getByText(/R v Jordan, 2016 SCC 27\./)).toBeTruthy()
  })

  it('shows user-provided notice when pinpoint is entered', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i })
    await user.type(input, "s 7")

    expect(screen.getByText(/pinpoint is user-provided/i)).toBeTruthy()
    expect(screen.getByText(/not database-verified/i)).toBeTruthy()
  })

  it('hides user-provided notice when pinpoint text is empty', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))
    // Input is visible and empty — notice should NOT appear
    expect(screen.queryByText(/pinpoint is user-provided/i)).toBeNull()
  })

  it('prefills pinpoint from API response and opens input automatically', () => {
    render(<CitationCard item={{ ...BASE_CITATION, pinpoint: "at para 2" }} sourceInput="" />)

    // Input should be visible and prefilled
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i }) as HTMLInputElement
    expect(input.value).toBe('at para 2')
  })

  it('state-lock: user clears prefilled pinpoint → re-render does not re-prefill', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <CitationCard item={{ ...BASE_CITATION, pinpoint: "at para 2" }} sourceInput="r v leo" />
    )

    // Verify prefilled
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i }) as HTMLInputElement
    expect(input.value).toBe('at para 2')

    // User clears it
    await user.clear(input)
    expect(input.value).toBe('')

    // Simulate re-render with identical props (same sourceInput)
    rerender(
      <CitationCard item={{ ...BASE_CITATION, pinpoint: "at para 2" }} sourceInput="r v leo" />
    )

    // Field must stay empty (state-lock: same query → no re-prefill)
    const inputAfter = screen.getByRole('textbox', { name: /pinpoint reference/i }) as HTMLInputElement
    expect(inputAfter.value).toBe('')
  })

  it('cross-query: new query resets state-lock so new pinpoint prefills', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <CitationCard
        item={{ ...BASE_CITATION, pinpoint: "at para 2" }}
        sourceInput="r v leo at para 2"
      />
    )

    // Verify prefill for query A
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i }) as HTMLInputElement
    expect(input.value).toBe('at para 2')

    // User clears it within same query
    await user.clear(input)
    expect(input.value).toBe('')

    // Re-render with same query → field stays empty (state-lock)
    rerender(
      <CitationCard
        item={{ ...BASE_CITATION, pinpoint: "at para 2" }}
        sourceInput="r v leo at para 2"
      />
    )
    expect(
      (screen.getByRole('textbox', { name: /pinpoint reference/i }) as HTMLInputElement).value
    ).toBe('')

    // New query B (different sourceInput) → lock resets → prefills B's pinpoint
    rerender(
      <CitationCard
        item={{ ...BASE_CITATION, citation: "R v Oakes, [1986] 1 SCR 103.", pinpoint: "at para 5" }}
        sourceInput="R v Oakes at para 5"
      />
    )
    const inputB = screen.getByRole('textbox', { name: /pinpoint reference/i }) as HTMLInputElement
    expect(inputB.value).toBe('at para 5')
  })
})

describe('CitationCard — period concatenation logic', () => {

  it('strips trailing period and re-adds after pinpoint', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i })
    await user.type(input, "at para 47")

    // Expected: "R v Jordan, 2016 SCC 27 at para 47." (no double period)
    const rendered = screen.getByText(/R v Jordan, 2016 SCC 27/)
    expect(rendered.textContent).toContain("at para 47.")
    expect(rendered.textContent).not.toContain("27..")
  })

  it('handles citation without trailing period gracefully', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={{
      citation: "Smith v Jones, 2023 ONCA 100",
    }} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i })
    await user.type(input, "at para 10")

    expect(screen.getByText(/Smith v Jones, 2023 ONCA 100 at para 10\./)).toBeTruthy()
  })

  it('removes trailing whitespace before period', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={{
      citation: "Criminal Code, RSC 1985, c C-46, s 718.2(e).   ",
    }} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i })
    await user.type(input, "s 7")

    expect(screen.getByText(/Criminal Code/).textContent).toContain("718.2(e) s 7.")
  })
})

describe('CitationCard — copy includes pinpoint', () => {

  it('copies full citation including pinpoint when present', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    await user.click(screen.getByText(/add pinpoint/i))
    const input = screen.getByRole('textbox', { name: /pinpoint reference/i })
    await user.type(input, "at para 47")

    await user.click(screen.getByRole('button', { name: /copy citation/i }))

    expect(mockCopy).toHaveBeenCalledTimes(1)
    expect(mockCopy).toHaveBeenCalledWith("R v Jordan, 2016 SCC 27 at para 47.")
  })

  it('copies original citation when no pinpoint is added', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)

    await user.click(screen.getByRole('button', { name: /copy citation/i }))

    expect(mockCopy).toHaveBeenCalledWith("R v Jordan, 2016 SCC 27.")
  })
})

describe('CitationCard — placeholder by source type', () => {

  it('shows case placeholder for case-like source types', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <CitationCard item={{ ...BASE_CITATION, source_type: "case" }} sourceInput="" />
    )
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i }))
      .toHaveProperty('placeholder', 'e.g. at para 47 or at paras 47–49')
  })

  it('shows statute placeholder for legislation', async () => {
    const user = userEvent.setup()
    render(
      <CitationCard item={{ ...BASE_CITATION, source_type: "legislation" }} sourceInput="" />
    )
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i }))
      .toHaveProperty('placeholder', 'e.g. s 7(2)(c) or ss 1–3')
  })

  it('shows bill placeholder for bill', async () => {
    const user = userEvent.setup()
    render(
      <CitationCard item={{ ...BASE_CITATION, source_type: "bill" }} sourceInput="" />
    )
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i }))
      .toHaveProperty('placeholder', 'e.g. cl 15(1)(a) or cl 5')
  })

  it('shows treaty placeholder for treaty', async () => {
    const user = userEvent.setup()
    render(
      <CitationCard item={{ ...BASE_CITATION, source_type: "treaty" }} sourceInput="" />
    )
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i }))
      .toHaveProperty('placeholder', 'e.g. art 14 or art 14(2)')
  })

  it('shows book/journal placeholder for book', async () => {
    const user = userEvent.setup()
    render(
      <CitationCard item={{ ...BASE_CITATION, source_type: "book" }} sourceInput="" />
    )
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i }))
      .toHaveProperty('placeholder', 'e.g. at 47 or at ch 7')
  })

  it('shows webpage placeholder for website', async () => {
    const user = userEvent.setup()
    render(
      <CitationCard item={{ ...BASE_CITATION, source_type: "website" }} sourceInput="" />
    )
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i }))
      .toHaveProperty('placeholder', 'e.g. at para 6 or heading (not official)')
  })

  it('shows unknown placeholder for unrecognised source type', async () => {
    const user = userEvent.setup()
    render(
      <CitationCard item={{ ...BASE_CITATION, source_type: "unknown_thing" }} sourceInput="" />
    )
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i }))
      .toHaveProperty('placeholder', 'e.g. at para / s / art / at page')
  })

  it('falls back to unknown placeholder when source_type is undefined', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i }))
      .toHaveProperty('placeholder', 'e.g. at para / s / art / at page')
  })
})

describe('CitationCard — duplicate pinpoint detection', () => {

  it('shows warning when base citation contains at para', () => {
    render(<CitationCard item={{ citation: "R v Smith, 2020 SCC 10 at para 35." }} sourceInput="" />)
    expect(screen.getByText(/may already contain a pinpoint/i)).toBeTruthy()
  })

  it('shows warning when base citation contains s \\d (statute section)', () => {
    render(<CitationCard item={{ citation: "Criminal Code, RSC 1985, c C-46, s 718.2(e)." }} sourceInput="" />)
    expect(screen.getByText(/may already contain a pinpoint/i)).toBeTruthy()
  })

  it('shows warning when base citation contains art (article)', () => {
    render(<CitationCard item={{ citation: "Charter of the United Nations, art 2(4)." }} sourceInput="" />)
    expect(screen.getByText(/may already contain a pinpoint/i)).toBeTruthy()
  })

  it('shows warning when base citation contains at ch (chapter)', () => {
    render(<CitationCard item={{ citation: "Jane Smith, *Book Title*, 2nd ed (Publisher, 2020) at ch 3." }} sourceInput="" />)
    expect(screen.getByText(/may already contain a pinpoint/i)).toBeTruthy()
  })

  it('shows warning when base citation contains at \\d+ (bare pinpoint)', () => {
    render(<CitationCard item={{ citation: "Jane Smith, *Book Title* (Publisher, 2020) at 123." }} sourceInput="" />)
    expect(screen.getByText(/may already contain a pinpoint/i)).toBeTruthy()
  })

  it('does NOT show warning for clean citation without pinpoint', () => {
    render(<CitationCard item={BASE_CITATION} sourceInput="" />)
    expect(screen.queryByText(/may already contain a pinpoint/i)).toBeNull()
  })

  it('still lets user open pinpoint input when warning is shown', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={{ citation: "R v Smith, 2020 SCC 10 at para 35." }} sourceInput="" />)
    expect(screen.getByText(/may already contain a pinpoint/i)).toBeTruthy()
    await user.click(screen.getByText(/add pinpoint/i))
    expect(screen.getByRole('textbox', { name: /pinpoint reference/i })).toBeTruthy()
  })

  it('shows warning inside the open input when citation has pinpoint', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={{ citation: "R v Smith, 2020 SCC 10 at para 35." }} sourceInput="" />)
    await user.click(screen.getByText(/add pinpoint/i))
    // The inline warning should remain visible inside the input area
    expect(screen.getByText(/may already contain a pinpoint/i)).toBeTruthy()
  })
})

describe('CitationCard — feedback contract', () => {

  it('posts backend-contract fields: verdict/input/output (no legacy vote)', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="r v jordan" />)

    await user.click(screen.getByRole('button', { name: /mark as accurate/i }))

    expect(mockPostFeedback).toHaveBeenCalledTimes(1)
    const payload = mockPostFeedback.mock.calls[0][0]
    expect(payload.verdict).toBe('up')
    expect(payload.input).toBe('r v jordan')
    expect(payload.output).toBe("R v Jordan, 2016 SCC 27.")
    expect(payload).not.toHaveProperty('vote')
    expect(payload).not.toHaveProperty('citation')
  })

  it('posts verdict "down" when marked incorrect', async () => {
    const user = userEvent.setup()
    render(<CitationCard item={BASE_CITATION} sourceInput="r v jordan" />)

    await user.click(screen.getByRole('button', { name: /mark as incorrect/i }))

    expect(mockPostFeedback).toHaveBeenCalledTimes(1)
    const payload = mockPostFeedback.mock.calls[0][0]
    expect(payload.verdict).toBe('down')
    expect(payload.input).toBe('r v jordan')
  })
})
