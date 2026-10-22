export const meta = {
  name: 'livery-refine',
  description: 'Fartstriber refinement: a quick fix (one round on the owner\'s note) or a full round (rounds of a fresh livery-builder, the sim and fresh livery-critics until the owner\'s note and every taste row pass, it plateaus, the budget of 4 runs out or a fix is blocked)',
  whenToUse: 'The refine step of livery-iterate, after the concept pick or a Change at the final review. Args: {design, strategy: quick or full, note, endorsed, pins, adopt}. Stops before livery review.',
  phases: [
    { title: 'Build', detail: 'a fresh livery-builder each round: the owner\'s note, the fixes, check, sim' },
    { title: 'Judge', detail: 'round, then in a full round compare in both orders: a fresh livery-critic each' },
    { title: 'Fork', detail: 'on a plateau or rework: three variants by fresh builders, ranked pairwise' },
  ],
}

// args: {design, strategy: 'quick' or 'full' (the default: a concept pick has none), note: the owner's
// direction, verbatim, when the session has it, endorsed: the critic suggestions they ticked, in the critic's
// words, pins: their pins as the review JSON gives them ({line, path}: "on <view>, over <element ids>: <note>"
// and its crop), adopt: a variant they picked at a plateau, for round 1 to adopt, judge_first: round 1 judges the
// checked build as it stands, for a build whose last fixes are already answered}.
//
// quick: one builder round on exactly the note, its pins and the endorsed suggestions, one sim run, a round critic
// (the note, each pin, the suggestions and every taste row), no compare or adversary, then back to the owner.
// full: up to BUDGET rounds, stopping at the first of: livery critic status says ready (the note, each pin and each
// endorsed suggestion judged resolved on sim shots, or baked renders that stand in for them, every taste row passing,
// nothing blocked); a plateau the fork doesn't break; the budget; a block (a blocked fix or kit fault goes to the
// owner at once, and stops the loop once no fix they would send the car back for is left, or when the build
// can't be judged). The critic's own fixes are worked in those rounds but never hold the car back. Ready is status's call, never the workflow's. The workflow ends where
// the owner decides: it never runs livery review.
// Once the kit's capture passed livery bake check, rounds are judged on baked renders, without the sim
// (docs/architecture.md, Design sessions). A baked round is ready only where its baked views stand in for sim
// shots (the check covered every finish the build uses). Where they don't, the budget's last round, and the round
// after a baked one that only its being baked held back, are judged on sim shots.

const VARIANTS = 3
const PAIR = '__' // joins a pair's names in its pack folder and answer file, as livery.critic does
const BUDGETS = { quick: 1, full: 4 }

const DESIGN = args && args.design
const STRATEGY = (args && args.strategy) || 'full'
const QUICK = STRATEGY === 'quick'
const BUDGET = BUDGETS[STRATEGY]
const NOTE = (args && args.note) || ''
const ENDORSED = (args && args.endorsed) || []
const PINS = (args && args.pins) || []
const ADOPT = (args && args.adopt) || ''
const JUDGE_FIRST = Boolean(args && args.judge_first) // round 1 judges the build as it stands, no builder first
if (typeof DESIGN !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(DESIGN)) throw new Error('args.design: a design name')
if (!BUDGET) throw new Error(`args.strategy: one of ${Object.keys(BUDGETS).join(', ')}`)
if (!Array.isArray(ENDORSED) || ENDORSED.some(e => typeof e !== 'string' || !e.trim()))
  throw new Error('args.endorsed: the critic suggestions the owner ticked, as text')
if (!Array.isArray(PINS) || PINS.some(p => !p || typeof p.line !== 'string' || !p.line.trim()))
  throw new Error('args.pins: the owner\'s pins as the review JSON gives them, each with its line and the path of its crop')
if (ADOPT && !/^[a-z0-9][a-z0-9-]*$/.test(ADOPT)) throw new Error('args.adopt: the variant to adopt first')

// The critic's answers, in the formats livery.critic validates; ids, rubric keys and taste rows it checks.
const STR = { type: 'string' }
const STRS = { type: 'array', items: STR }
const BOOL = { type: 'boolean' }
const RESOLVED = { type: 'string', enum: ['resolved', 'partly', 'not'] }
const ROUND = {
  type: 'object',
  required: ['previous', 'owner_note', 'endorsed', 'pins', 'taste', 'scores', 'overall', 'verdict', 'fixes', 'lint_missed', 'blocked'],
  properties: {
    previous: {
      type: 'array',
      items: {
        type: 'object',
        required: ['fix', 'status', 'evidence'],
        properties: { fix: STR, status: RESOLVED, evidence: STR },
      },
    },
    // The owner's latest note, when the pack carries one; null when it doesn't.
    owner_note: {
      type: ['object', 'null'],
      required: ['status', 'evidence'],
      properties: { status: RESOLVED, evidence: STR },
    },
    // One per critic suggestion the owner's note endorsed, in order; [] when it endorsed none.
    endorsed: {
      type: 'array',
      items: { type: 'object', required: ['status', 'evidence'], properties: { status: RESOLVED, evidence: STR } },
    },
    // One per pin of the owner's note, in order; [] when it has none.
    pins: {
      type: 'array',
      items: { type: 'object', required: ['status', 'evidence'], properties: { status: RESOLVED, evidence: STR } },
    },
    taste: {
      type: 'array',
      items: { type: 'object', required: ['id', 'pass', 'where'], properties: { id: STR, pass: BOOL, where: STR } },
    },
    scores: { type: 'object', additionalProperties: { type: 'integer', minimum: 1, maximum: 5 } },
    overall: { type: 'integer', minimum: 1, maximum: 10 },
    verdict: { type: 'string', enum: ['iterate', 'rework'] },
    // Every suggestion, the most important first.
    fixes: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'detail', 'where', 'optional'],
        properties: { title: STR, detail: STR, where: STR, optional: BOOL },
      },
    },
    lint_missed: STRS,
    blocked: STRS,
  },
}
const COMPARE = {
  type: 'object',
  required: ['better', 'where'],
  properties: { better: { type: 'string', enum: ['A', 'B', 'same'] }, where: STR },
}
const PAIR_ANSWER = {
  type: 'object',
  required: ['winner', 'why'],
  properties: { winner: { type: 'string', enum: ['A', 'B', 'tie'] }, why: STR },
}
// The builder's report.
const SKETCH = {
  type: 'object',
  required: ['design', 'rendered', 'summary', 'problems'],
  properties: { design: STR, rendered: BOOL, summary: STR, problems: STRS },
}
// character and sim stop the loop for the owner. blocked and kit go to the owner too, but a checked build
// in the sim is still judged and its other fixes worked; the loop stops on them once no fix the owner
// would send the car back for is left. A kit gap the round worked around, or a sim run it didn't need,
// goes in notes.
const STOPS = (what) => ({ type: 'array', items: STR, description: `${what}; [] when there is none` })
const BUILT = {
  type: 'object',
  required: ['design', 'checked', 'sim_run', 'blocked', 'kit', 'character', 'sim', 'constraints', 'summary'],
  properties: {
    design: STR, checked: BOOL, sim_run: STR,
    blocked: STOPS('fixes this round could not make inside the design folder'),
    kit: STOPS('kit faults found this round that stop a fix (not gaps it designed around)'),
    character: { type: 'string', description: "a change of the brief's character the fixes would need; an empty string when none" },
    sim: { type: 'string', description: "what the owner must do before the next sim round can run; an empty string when nothing" },
    notes: { type: 'array', items: STR, description: 'anything else for the owner: kit gaps worked around, known limits' },
    constraints: STRS, summary: STR,
  },
}
// A builder sometimes answers 'none', or an empty pair of quotes, in a stop field instead of leaving it empty.
const NONE = /^\s*(none|no |nothing|not run|n\/a|-\s*$)/i
const bare = (x) => x.replace(/^[\s"'`]+|[\s"'`]+$/g, '')
const real = (xs) => (Array.isArray(xs) ? xs : xs ? [xs] : []).filter(x => typeof x === 'string' && bare(x) && !NONE.test(bare(x)))
// What the CLI steps print, as the clerk reads it back.
const STATUS = {
  type: 'object',
  required: ['round', 'ready', 'holds', 'ship', 'why', 'plateau'],
  properties: {
    design: STR, round: { type: 'integer' }, ready: BOOL, holds: STRS, ship: BOOL, call: { type: ['string', 'null'] },
    why: STRS, plateau: BOOL, baked: BOOL,
  },
}
const FOLDER = { type: 'object', required: ['ok'], properties: { ok: BOOL, folder: STR, error: STR } }
const FOLDERS = { type: 'object', required: ['ok'], properties: { ok: BOOL, folders: STRS, error: STR } }
const STATE = { type: 'object', required: ['ok'], properties: { ok: BOOL, status: STATUS, error: STR } }
const COMPARED = {
  type: 'object',
  required: ['ok'],
  properties: { ok: BOOL, result: { type: 'string', enum: ['better', 'worse', 'same'] }, status: STATUS, error: STR },
}
const RANKED = { type: 'object', required: ['ok'], properties: { ok: BOOL, ranking: STRS, error: STR } }
const BAKE = {
  type: 'object',
  required: ['ok', 'checked', 'usable'],
  properties: { ok: BOOL, checked: BOOL, usable: BOOL, error: STR },
}

const indent = text => text.split('\n').map(l => `  ${l}`).join('\n')

function clerk(label, phase, steps, schema) {
  return agent(
    'You run Fartstriber CLI steps for a workflow, from the repository root, exactly as listed. Edit, create ' +
      'and delete no file except the ones a step names, and run nothing else. Stop at the first step that ' +
      'fails and return ok false with its error line.\n\n' + steps,
    { label, phase, schema, effort: 'low' },
  )
}

function critic(folder, mode, schema, label, phase, refused) {
  return agent(
    `Pack: ${folder}\nMode: ${mode}. Read the pack's manifest.json first, then every file it lists.` +
      (refused ? `\nAn earlier answer to this pack was refused when it was recorded: ${refused}. Answer so it is accepted.` : ''),
    { agentType: 'livery-critic', schema, label, phase },
  )
}

const write = (n, file, value) =>
  `${n}. Write this JSON, exactly as given, to a new file named ${file} in your scratchpad directory:\n${JSON.stringify(value)}\n`
const status = n => `${n}. uv run livery critic status ${DESIGN}\nReturn ok and the JSON the last command prints, as status.`

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
  const judge = ([a, b]) => critic(`${packed.folder}${sep}${a}${PAIR}${b}`, 'pair', PAIR_ANSWER, `${a} vs ${b}`, phase)
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
  return { ranking: ranked.ranking }
}

const bakedFlag = baked => (baked ? ' --baked' : '')

// A round: the pack, a fresh critic, the record (a refused verdict goes to one more fresh critic).
async function judgeRound(n, baked) {
  const packed = await clerk(`r${n} pack`, 'Judge',
    `1. uv run livery critic pack ${DESIGN}${bakedFlag(baked)}\nReturn ok and the folder it prints (its last line), exactly as printed.`,
    FOLDER)
  if (!packed || !packed.ok || !packed.folder) return { error: `round ${n} pack: ${packed ? packed.error : 'no answer'}` }
  let refused = ''
  for (let attempt = 1; attempt <= 2; attempt++) {
    const verdict = await critic(packed.folder, 'round', ROUND, `r${n} critic`, 'Judge', refused)
    if (!verdict) continue
    const rec = await clerk(`r${n} record`, 'Judge',
      write(1, `${DESIGN}-round${n}.json`, verdict) +
        `2. uv run livery critic record ${DESIGN} <that file>\n` + status(3),
      STATE)
    if (rec && rec.ok && rec.status) return { verdict, status: rec.status }
    refused = rec ? rec.error : 'no answer from the recording step'
  }
  return { error: `round ${n}: no critic verdict could be recorded: ${refused}` }
}

// This round against the last, in both orders, by two fresh critics.
async function compareRounds(n, baked) {
  const packs = await clerk(`r${n} compare packs`, 'Judge',
    `1. uv run livery critic pack ${DESIGN} --compare${bakedFlag(baked)}\nReturn ok and the two folders it prints, in the order printed.`,
    FOLDERS)
  if (!packs || !packs.ok || !packs.folders || packs.folders.length !== 2)
    return { error: `round ${n} compare packs: ${packs ? packs.error : 'no answer'}` }
  const ask = i => critic(packs.folders[i], 'compare', COMPARE, `r${n} compare ${i + 1}`, 'Judge')
  let answers = await parallel([0, 1].map(i => () => ask(i)))
  answers = await parallel([0, 1].map(i => () => answers[i] || ask(i)))
  if (answers.some(v => !v)) return { error: `round ${n}: a compare critic gave no answer` }
  const rec = await clerk(`r${n} compare`, 'Judge',
    write(1, `${DESIGN}-compare${n}-1.json`, answers[0]) + write(2, `${DESIGN}-compare${n}-2.json`, answers[1]) +
      `3. uv run livery critic compare ${DESIGN} <the first file> <the second file>\n` + status(4) +
      ' Also return as result the word step 3 printed: better, worse or same.',
    COMPARED)
  if (!rec || !rec.ok || !rec.status) return { error: `round ${n} compare: ${rec ? rec.error : 'no answer'}` }
  return { result: rec.result, status: rec.status }
}

// A baked round whose renders can't stand in for sim shots, that would go back to the owner if it had been
// judged on sim shots.
const onlyBakedHolds = st => st.baked && st.holds.length > 0 && st.holds.every(w => /baked renders/i.test(w))

const SOURCES = [
  'Take the move from the system of the brief\'s first reference row (its pictures are in .cache/references/).',
  'Take the move from the system of the brief\'s second reference row, or of another yes row of references.md.',
  'Take the move from a real object or a one-sentence story, and name it in notes.md.',
]

async function forkVariants(why, st) {
  const alts = SOURCES.slice(0, VARIANTS).map((_, k) => `${DESIGN}-alt${k + 1}`)
  const open = st.why
  const made = await parallel(alts.map((alt, k) => () => agent(
    `design: ${alt}\nSketch mode: variant ${k + 1} of ${alts.length} of designs/${DESIGN}/, which has stalled: ` +
      `${why}. The variants and the design as it stands are ranked pairwise on flat views and car.png, and ` +
      'the winner carries on.\n\n' +
      `Make designs/${alt}/ from designs/${DESIGN}/: read its design.py, brief.md and any sponsors/ folder and ` +
      'write them into your folder, over whatever an earlier fork left there. Then make one structural move, ' +
      `a different big idea or 10 m system rather than a patch to a detail. ${SOURCES[k]} Keep the brief's ` +
      'character. Put the move first in notes.md.\n\n' +
      `What holds it back:\n${indent(open.map(o => `- ${o}`).join('\n'))}`,
    { agentType: 'livery-builder', schema: SKETCH, label: alt, phase: 'Fork' })))
  const variants = alts.filter((_, k) => made[k] && made[k].rendered)
  if (!variants.length) return { winner: DESIGN, ranking: [DESIGN], variants: [] }
  const ranked = await rankPairs([DESIGN, ...variants], 'Fork')
  if (ranked.error) return { error: ranked.error }
  return { winner: ranked.ranking[0], ranking: ranked.ranking, variants }
}

function buildPrompt(n, adopt, baked) {
  let task = `design: ${DESIGN}\n` + (QUICK
    ? 'Build mode: a quick fix, one round on exactly what the owner asked for, which they see next. Change ' +
      'what their note, their pins and the suggestions they endorsed ask, and nothing else. Answer every other fix of ' +
      'the last verdict skipped ("a quick fix: only the owner\'s note this round").\n'
    : `Build mode: round ${n} of up to ${BUDGET} of an autonomous refinement, which goes back to the owner once ` +
      'the critic judges their note, each pin and the suggestions they endorsed resolved and every taste row passes. ' +
      'Their direction outranks the critic\'s fixes.\n')
  // Every round answers to the same direction: the last verdict's owner_note and endorsed say how far it got.
  if (NOTE) task += `The owner's direction, verbatim:\n${indent(NOTE)}\n`
  if (ENDORSED.length)
    task += 'Critic suggestions the owner endorsed (the critic\'s words, not theirs):\n' +
      indent(ENDORSED.map(e => `- ${e}`).join('\n')) + '\n'
  if (PINS.length)
    task += 'The owner\'s pins, each a spot they boxed on a picture with what to change there; look at each crop, ' +
      'the box outlined:\n' + indent(PINS.map(p => `- ${p.line}${p.path ? ` (crop: ${p.path})` : ''}`).join('\n')) + '\n'
  if (!NOTE && !ENDORSED.length && !PINS.length)
    task += `Start from the owner's latest decision on this design in their prefs/<person>/decisions.jsonl (concept:${DESIGN} or ` +
      `final:${DESIGN}: their note, their pins and any critic suggestions they endorsed) and its entry in their log.md.\n`
  if (adopt)
    task += `First adopt designs/${adopt}/, the variant that won ${n === 1 ? 'the owner\'s pick' : 'the pairwise ranking'}: copy its ` +
      'design.py (and any sponsors/ folder) over this design\'s, keep brief.md\'s character, and write the move ' +
      'into notes.md. Leave the variant folders alone.\n'
  if (baked)
    return task + 'This round\'s critic judges baked renders of your checked build: use baked views throughout, ' +
      'and run no livery preview sim. Work the last verdict\'s fixes in verdicts.jsonl as your procedure says, then ' +
      'livery check, livery build and livery preview flat --baked, and answer the fixes from the baked views.'
  return task + 'This round\'s critic judges sim shots. Work the last verdict\'s fixes in verdicts.jsonl as your ' +
    'procedure says, then livery check, livery build and livery preview sim, and answer the fixes.'
}

const history = []
const constraints = new Set()
const notes = new Set() // for the owner, not stopping the loop
const held = new Set() // blocked fixes and kit faults for the owner, carried while other fixes are worked
let last = null // the latest livery critic status
let fork = null

function finish(stop, extra) {
  const more = extra || {}
  const review = more.review !== undefined ? more.review
    : ['ready', 'quick', 'budget'].includes(stop) ? `uv run livery review ${DESIGN}` : null
  const shown = (review || '').split(' ')
  return {
    design: DESIGN,
    stop,
    rounds: history.length,
    history,
    status: last,
    review,
    for_owner: [...(more.for_owner || []), ...held, ...notes],
    constraints: [...constraints],
    fork: fork && { ranking: fork.ranking, winner: fork.winner },
    remove: fork ? fork.variants.filter(v => !shown.includes(v)) : [],
  }
}

// checked: baked renders may judge a round; usable: they stand in for the checked build's sim shots, so a
// round judged on them can go back to the owner.
const bakeStatus = async () => {
  const st = await clerk('bake status', 'Build',
    `1. uv run livery bake status --design ${DESIGN} --json\nReturn ok and, as checked and usable, the values of ` +
      '"checked" and "usable" in the JSON it prints.',
    BAKE)
  return { checked: Boolean(st && st.ok && st.checked), usable: Boolean(st && st.ok && st.usable) }
}
const bakeState = await bakeStatus()
const BAKED_OK = bakeState.checked
log(!BAKED_OK ? 'every round is judged on sim shots: the bake isn\'t checked'
  : bakeState.usable ? 'rounds are judged on baked renders, which stand in for sim shots'
  : 'middle rounds are judged on baked renders; the last on sim shots unless the bake comes to stand in')

let adopt = ADOPT || null
let simNext = false // the last baked round would have gone back to the owner on sim shots
for (let n = 1; n <= BUDGET; n++) {
  // The budget's last round is baked only where the bake stands in for the build as it is now.
  const standsIn = n < BUDGET || (BAKED_OK && !simNext && (n === 1 ? bakeState : await bakeStatus()).usable)
  const baked = BAKED_OK && !simNext && standsIn
  simNext = false
  const asIs = JUDGE_FIRST && n === 1
  // Judging the design as it stands still needs a current check and previews of that build: a library or
  // kit change since the last build makes the stored check stale.
  const rebuilt = asIs
    ? await clerk('rebuild', 'Build',
        `1. uv run livery build ${DESIGN}
2. ${baked ? `uv run livery preview flat ${DESIGN} --baked` : `uv run livery preview sim ${DESIGN}`}
` +
          'Return ok and, as folder, the last line step 2 printed.',
        FOLDER)
    : null
  if (asIs && !(rebuilt && rebuilt.ok))
    return finish('error', { for_owner: [`round 1: the design as it stands doesn't build: ${(rebuilt && rebuilt.error) || 'no report'}`] })
  const built = asIs
    ? { checked: true, sim_run: baked ? '' : rebuilt.folder, blocked: [], kit: [], character: '', sim: '', constraints: [], summary: 'judged as it stands' }
    : await agent(buildPrompt(n, adopt, baked), {
        agentType: 'livery-builder', schema: BUILT, label: `r${n} build${baked ? ' (baked)' : ''}`, phase: 'Build',
      })
  const entry = { round: n, adopted: adopt, baked }
  history.push(entry)
  adopt = null
  if (!built) return finish('error', { for_owner: [`round ${n}: the builder gave no report`] })
  entry.built = built.summary
  for (const c of built.constraints) constraints.add(c)
  const [blocked, kit, character, sim] = [real(built.blocked), real(built.kit), real(built.character), real(built.sim)]
  for (const n of real(built.notes)) notes.add(n)
  for (const b of blocked) held.add(`blocked: ${b}`)
  for (const k of kit) held.add(`kit fault: ${k}`)
  const raised = [...character.map(c => `character conflict: ${c}`), ...sim.map(x => `the sim needs: ${x}`)]
  const unjudged = !built.checked || (!baked && !built.sim_run)
  if (raised.length || (unjudged && (blocked.length || kit.length))) {
    const stop = character.length ? 'character' : sim.length ? 'sim' : blocked.length ? 'blocked' : 'kit'
    return finish(stop, { for_owner: raised })
  }
  if (unjudged)
    return finish('error', { for_owner: [`round ${n}: no checked build${baked ? '' : ' in the sim'}: ${built.summary}`] })

  const judged = await judgeRound(n, baked)
  if (judged.error) return finish('error', { for_owner: [judged.error] })
  const verdict = judged.verdict
  last = judged.status
  Object.assign(entry, {
    overall: verdict.overall, call: verdict.verdict, fixes: verdict.fixes.map(f => f.title),
    owner_note: verdict.owner_note ? verdict.owner_note.status : null,
    endorsed: (verdict.endorsed || []).map(e => e.status),
    pins: (verdict.pins || []).map(p => p.status),
  })
  for (const b of verdict.blocked) held.add(`blocked, as the critic sees it: ${b}`)
  if (verdict.blocked.length && !verdict.fixes.some(f => !f.optional)) return finish('blocked')
  // A quick fix has no compare; a full round needs one only to see a plateau, once it isn't ready.
  if (!QUICK && !last.ready && last.round > 1) {
    const compared = await compareRounds(n, baked)
    if (compared.error) return finish('error', { for_owner: [compared.error] })
    entry.compare = compared.result
    last = compared.status
  }
  if (baked && onlyBakedHolds(last)) {
    simNext = true
    log(`round ${n}: only its baked shots hold it back; the next round is judged on sim shots`)
  }
  const against = { better: ', better than the last round', worse: ', worse than the last round', same: ', the same as the last round' }
  const noted = entry.owner_note ? `, the owner's note ${entry.owner_note}` : ''
  log(`round ${n}${baked ? ' (baked)' : ''}: ${verdict.verdict}, ${verdict.overall}/10${noted}${against[entry.compare] || ''}${last.ready ? ', ready for the owner' : ''}`)
  if (last.ready) return finish('ready')
  if (QUICK) return finish('quick', { for_owner: last.holds })

  const why = last.plateau ? 'two compares in a row came out the same'
    : verdict.verdict === 'rework' ? 'the critic called rework: the concept is what fails' : null
  if (!why || n === BUDGET) continue
  if (fork)
    return finish('plateau', {
      for_owner: [`${why}, after adopting ${fork.winner} from a fork`],
      review: `uv run livery review ${DESIGN}`,
    })
  fork = await forkVariants(why, last)
  if (fork.error) return finish('error', { for_owner: [fork.error] })
  log(`fork: ${fork.ranking.join(' > ')}`)
  if (fork.winner === DESIGN)
    return finish('plateau', {
      for_owner: [`${why}, and no variant beat it pairwise (${fork.ranking.join(' > ')})`],
      review: `uv run livery review ${[DESIGN, ...fork.variants].join(' ')}`,
    })
  adopt = fork.winner
}
return finish('budget')
