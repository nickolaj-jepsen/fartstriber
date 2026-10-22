export const meta = {
  name: 'livery-concepts',
  description: 'Fartstriber concepts: 6-8 sketches from different sources, ranked pairwise by fresh critics; the top 3 built to a passing check and shot in the sim for the concept pick',
  whenToUse: 'The concepts step of livery-iterate, after the brief. Args: {brief, names, references}. Stops before livery review.',
  phases: [
    { title: 'Sources', detail: 'one concept brief per name, each from a different source' },
    { title: 'Sketch', detail: 'a livery-builder per concept, flat views and car.png only' },
    { title: 'Rank', detail: 'every pair in both orders, a fresh livery-critic each, then Bradley-Terry' },
    { title: 'Build', detail: 'the top 3 to a passing check and a sim run' },
  ],
}

// args: {brief: the owner's words and the constraints, names: 6-8 new design names, references: the 2-3
// references.md rows the brief names}. The workflow ends where the owner decides: it never runs livery review.

const TOP = 3
const PAIR = '__' // joins a pair's names in its pack folder and answer file, as livery.critic does

const brief = args && args.brief
const names = (args && args.names) || []
const rows = (args && args.references) || []
if (typeof brief !== 'string' || !brief.trim()) throw new Error('args.brief: the owner\'s brief, as text')
if (!Array.isArray(names) || names.length < TOP + 1 || new Set(names).size !== names.length)
  throw new Error(`args.names: ${TOP + 1} or more different design names (6-8 is the plan)`)
if (names.some(n => !/^[a-z0-9][a-z0-9-]*$/.test(n)))
  throw new Error('args.names: lowercase letters, digits and hyphens')
if (!Array.isArray(rows) || !rows.length) throw new Error('args.references: the references.md rows the brief names')

const STR = { type: 'string' }
const STRS = { type: 'array', items: STR }
const CONCEPTS = {
  type: 'object',
  required: ['concepts'],
  properties: {
    concepts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'source', 'expected', 'move', 'references', 'at_50m', 'at_10m', 'at_1m'],
        properties: {
          name: STR, source: STR, expected: STR, move: STR,
          references: { type: 'array', items: { type: 'integer' } },
          at_50m: STR, at_10m: STR, at_1m: STR,
        },
      },
    },
    missing_pictures: { type: 'array', items: { type: 'integer' } },
  },
}
const SKETCH = {
  type: 'object',
  required: ['design', 'rendered', 'summary', 'problems'],
  properties: { design: STR, rendered: { type: 'boolean' }, summary: STR, problems: STRS },
}
// blocked, kit, character and sim stop the loop for the owner, so they hold only what does: a kit gap the
// round worked around, or a sim run it didn't need, goes in notes.
const STOPS = (what) => ({ type: 'array', items: STR, description: `${what}; [] when there is none` })
const BUILT = {
  type: 'object',
  required: ['design', 'checked', 'sim_run', 'blocked', 'kit', 'character', 'sim', 'constraints', 'summary'],
  properties: {
    design: STR, checked: { type: 'boolean' }, sim_run: STR,
    blocked: STOPS('fixes this round could not make inside the design folder'),
    kit: STOPS('kit faults found this round that stop a fix (not gaps it designed around)'),
    character: { type: 'string', description: "a change of the brief's character the fixes would need; '' when none" },
    sim: { type: 'string', description: "what the owner must do before the next sim round can run; '' when nothing" },
    notes: { type: 'array', items: STR, description: 'anything else for the owner: kit gaps worked around, known limits' },
    constraints: STRS, summary: STR,
  },
}
const PAIR_ANSWER = {
  type: 'object',
  required: ['winner', 'why'],
  properties: { winner: { type: 'string', enum: ['A', 'B', 'tie'] }, why: STR },
}
const FOLDER = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, folder: STR, error: STR } }
const RANKED = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, ranking: STRS, error: STR } }

const indent = text => text.split('\n').map(l => `  ${l}`).join('\n')

function clerk(label, phase, steps, schema) {
  return agent(
    'You run Fartstriber CLI steps for a workflow, from the repository root, exactly as listed. Edit, create ' +
      'and delete no file except the ones a step names, and run nothing else. Stop at the first step that ' +
      'fails and return ok false with its error line.\n\n' + steps,
    { label, phase, schema, effort: 'low' },
  )
}

// Concepts ranked by a fresh critic on every ordered pair, then Bradley-Terry in `livery critic rank`.
async function rankPairs(contenders, phase) {
  const packed = await clerk('pair packs', phase,
    contenders.map((n, i) => `${i + 1}. uv run livery preview flat ${n}`).join('\n') +
      `\n${contenders.length + 1}. uv run livery critic pack ${contenders.join(' ')} --pairs\n` +
      'Return ok and the folder the last command prints (its last line), exactly as printed.',
    FOLDER)
  if (!packed || !packed.ok || !packed.folder) return { error: `pair packs: ${packed ? packed.error : 'no answer'}` }
  const sep = packed.folder.includes('\\') ? '\\' : '/'
  const pairs = []
  for (const a of contenders) for (const b of contenders) if (a !== b) pairs.push([a, b])
  const judge = ([a, b]) => agent(
    `Pack: ${packed.folder}${sep}${a}${PAIR}${b}\nMode: pair. Read the pack's manifest.json first, then every file it lists.`,
    { agentType: 'livery-critic', schema: PAIR_ANSWER, label: `${a} vs ${b}`, phase })
  let answers = await parallel(pairs.map(p => () => judge(p)))
  const retry = pairs.filter((_, i) => !answers[i])
  if (retry.length) {
    log(`${retry.length} pair critics gave no answer; asking fresh ones`)
    const again = await parallel(retry.map(p => () => judge(p)))
    answers = answers.map((v, i) => v || again[retry.indexOf(pairs[i])])
  }
  if (answers.some(v => !v)) return { error: 'a pair has no critic answer after a retry' }
  const files = {}
  pairs.forEach(([a, b], i) => { files[`${a}${PAIR}${b}.json`] = answers[i] })
  const ranked = await clerk('rank', phase,
    '1. Make a new empty folder in your scratchpad directory and write each entry of this JSON map into it as ' +
      'its own file, the key as the file name and the value as its JSON content (a .py script in the ' +
      `scratchpad is quickest):\n${JSON.stringify(files)}\n` +
      `2. uv run livery critic rank ${contenders.join(' ')} --pairs <that folder>\n` +
      'Return ok and the ranking it prints ("a > b > c") as a list of names, best first.',
    RANKED)
  if (!ranked || !ranked.ok || !ranked.ranking || ranked.ranking.length !== contenders.length)
    return { error: `rank: ${ranked ? ranked.error || 'a ranking of the wrong length' : 'no answer'}` }
  const why = {}
  pairs.forEach(([a, b], i) => {
    const v = answers[i]
    const won = v.winner === 'A' ? a : v.winner === 'B' ? b : null
    for (const n of [a, b]) (why[n] = why[n] || []).push(`${won === n ? 'won' : won ? 'lost' : 'tied'} against ${n === a ? b : a}: ${v.why}`)
  })
  return { ranking: ranked.ranking, why }
}

// Sources: one agent writes every concept brief, so that no two share a source.
const planned = await agent(
  'You plan the concepts of a Fartstriber livery session. Create, edit and delete no file.\n\n' +
    `The owner's brief:\n${indent(brief)}\n\nThe reference rows it names: ${rows.join(', ')}.\n\n` +
    'Read .claude/skills/livery-design/SKILL.md (the concept brief template and the three distances), ' +
    'taste.md and rubric.md beside it, the yes rows of references.md and all of prefs/log.md, and look at ' +
    'the pictures of the named rows in .cache/references/<row>/ (list any row without pictures under ' +
    'missing_pictures).\n\n' +
    `Write one concept brief for each of these names: ${names.join(', ')}. Builders sketch them in parallel ` +
    'without seeing each other, so the differences have to be in the briefs. Every concept takes a different ' +
    'source and a structurally different big idea, not different colours: a favourite whose system it ' +
    'transplants (one concept per favourite at most), a real object or photo described in words, a ' +
    'one-sentence story. Each writes down the expected version of this brief that it must not be, and ' +
    'commits to one move completely. Each lists the 2-3 reference rows it draws on, from the brief\'s rows ' +
    'or other yes rows. Keep what the brief fixes (base colour, brands, the headline sponsor as it asks).',
  { label: 'concept briefs', phase: 'Sources', schema: CONCEPTS })
if (!planned) throw new Error('the concept planner gave no answer')
const byName = new Map(planned.concepts.map(c => [c.name, c]))
const concepts = names.map((name, i) => ({ ...(byName.get(name) || planned.concepts[i] || {}), name }))
if (concepts.some(c => !c.source)) throw new Error('the planner left a concept without a source')
if (planned.missing_pictures && planned.missing_pictures.length)
  log(`no cached pictures for rows ${planned.missing_pictures.join(', ')}: run livery references fetch`)

const conceptBrief = c =>
  `- Source: ${c.source}\n- The expected version, banned: ${c.expected}\n- The one move: ${c.move}\n` +
  `- References: ${c.references.join(', ')}\n- 50 m: ${c.at_50m}\n- 10 m: ${c.at_10m}\n- 1 m: ${c.at_1m}`

// Sketch: every concept at once, on flat views only.
const sketches = await parallel(concepts.map(c => () => agent(
  `design: ${c.name}\nSketch mode: a new concept, judged against ${concepts.length - 1} other sketches on its ` +
    'flat views and car.png alone.\n\n' +
    `The owner's brief:\n${indent(brief)}\n\nYour concept brief (keep its source and its move):\n${conceptBrief(c)}\n\n` +
    `Create designs/${c.name}/ with brief.md and design.py and sketch until livery preview flat shows the move.`,
  { agentType: 'livery-builder', schema: SKETCH, label: c.name, phase: 'Sketch' })))
const rendered = concepts.filter((c, i) => sketches[i] && sketches[i].rendered).map(c => c.name)
const unrendered = concepts.filter(c => !rendered.includes(c.name)).map(c => c.name)
if (unrendered.length) log(`no flat views from: ${unrendered.join(', ')}`)
if (rendered.length < TOP) throw new Error(`only ${rendered.length} sketches rendered: ${rendered.join(', ')}`)

// Rank: every pair in both orders.
const ranked = await rankPairs(rendered, 'Rank')
if (ranked.error) throw new Error(ranked.error)
const top = ranked.ranking.slice(0, TOP)
log(`ranking: ${ranked.ranking.join(' > ')}`)

// Build: the top 3 to a passing check and a sim run; their sim runs queue on the sim lock.
const built = await parallel(top.map((name, i) => () => agent(
  `design: ${name}\nBuild mode: this concept ranked ${i + 1} of ${rendered.length} in the critic's pairwise ` +
    'ranking and goes to the owner\'s concept pick. Keep the concept as it is: fix what livery check needs, ' +
    'then livery build and livery preview sim, so they see it in the sim. There are no critic fixes yet.\n\n' +
    `The pair critics on it:\n${indent(ranked.why[name].join('\n'))}`,
  { agentType: 'livery-builder', schema: BUILT, label: `${name} build`, phase: 'Build' })))

const forOwner = []
top.forEach((name, i) => {
  const b = built[i]
  if (!b) return forOwner.push(`${name}: the builder gave no report`)
  if (!b.checked) forOwner.push(`${name}: livery check doesn't pass: ${b.summary}`)
  if (b.sim) forOwner.push(`${name}: the sim needs: ${b.sim}`)
  if (b.character) forOwner.push(`${name}: character conflict: ${b.character}`)
  for (const k of b.kit) forOwner.push(`${name}: kit fault: ${k}`)
  for (const x of b.blocked) forOwner.push(`${name}: blocked: ${x}`)
  for (const x of b.notes || []) forOwner.push(`${name}: ${x}`)
})

return {
  ranking: ranked.ranking,
  top,
  review: `uv run livery review ${top.join(' ')}`,
  concepts: concepts.map(c => ({ name: c.name, source: c.source, move: c.move, expected: c.expected })),
  built: built.map((b, i) => b || { design: top[i], summary: 'no report' }),
  constraints: built.filter(Boolean).flatMap(b => b.constraints.map(x => `${b.design}: ${x}`)),
  for_owner: forOwner,
  unshown: [...ranked.ranking.slice(TOP), ...unrendered],
}
