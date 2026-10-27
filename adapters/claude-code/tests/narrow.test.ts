import { expect, test } from 'claude-code/testing'

import { narrowTables } from '../hooks/narrow'

test('a markdown table becomes one list line per row, labeled by header', () => {
  const md = [
    'Before.',
    '',
    '| Lamp | Brightest | Cheapest |',
    '|---|---|:---:|',
    '| Harbour | LED (98) | Halogen |',
    '| Pier | Xenon (87) |  |',
    '',
    'After.',
  ].join('\n')
  expect(narrowTables(md)).toBe(
    [
      'Before.',
      '',
      '- Harbour · Brightest: LED (98) · Cheapest: Halogen',
      '- Pier · Brightest: Xenon (87)',
      '',
      'After.',
    ].join('\n'),
  )
})

test('tables inside code blocks and lone pipes are left alone', () => {
  const md = ['```', '| a | b |', '|---|---|', '| 1 | 2 |', '```', 'a | b is fine'].join('\n')
  expect(narrowTables(md)).toBe(md)
})
