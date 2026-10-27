import { expect, test } from 'claude-code/testing'

import { askEntry, placeInDraft } from '../hooks/draft'

test('an empty box takes the entry', () => {
  expect(placeInDraft('', 0, 'A')).toEqual({ text: 'A', cursor: 1 })
})

test('the empty line under the cursor takes it', () => {
  expect(placeInDraft('one\n\nthree', 4, 'A')).toEqual({ text: 'one\nA\nthree', cursor: 5 })
})

test('else the next empty line below the cursor', () => {
  expect(placeInDraft('one\ntwo\n\nfour', 1, 'A')).toEqual({ text: 'one\ntwo\nA\nfour', cursor: 9 })
})

test('an empty line above the cursor is left alone; none below adds a line', () => {
  expect(placeInDraft('\ntwo', 3, 'A')).toEqual({ text: '\ntwo\nA', cursor: 6 })
})

test('an entry is the title, then the description', () => {
  expect(askEntry('Pick a lamp', 'LED or halogen')).toBe('Pick a lamp — LED or halogen')
  expect(askEntry('Pick a lamp', '')).toBe('Pick a lamp')
})
