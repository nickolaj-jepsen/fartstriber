// Runs a workflow script from .claude/workflows/ with scripted agents, for tests/test_workflows.py.
// Reads a scenario {script, args, answers: [[label regex, answer], ...]} as JSON on stdin; an answer
// {"$each": [...]} gives its values one call at a time. Prints {result, error, calls, logs}.
const fs = require('fs')

const scenario = JSON.parse(fs.readFileSync(0, 'utf8'))
const source = fs.readFileSync(scenario.script, 'utf8').replace(/^export const meta\s*=/m, 'const meta =')
const answers = scenario.answers.map(([pattern, answer]) => ({ re: new RegExp(pattern), answer, used: 0 }))
const calls = []
const logs = []

async function agent(prompt, opts) {
  const o = opts || {}
  calls.push({ label: o.label || '', agentType: o.agentType || null, phase: o.phase || null, prompt })
  const match = answers.find(a => a.re.test(o.label || ''))
  if (!match) throw new Error(`no scripted answer for agent ${JSON.stringify(o.label)}`)
  const value = match.answer && match.answer.$each ? match.answer.$each[match.used] : match.answer
  match.used++
  return value === undefined ? null : JSON.parse(JSON.stringify(value))
}
const parallel = thunks => Promise.all(thunks.map(t => t()))
const log = m => logs.push(String(m))

const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor
new AsyncFunction('args', 'agent', 'parallel', 'log', source)(scenario.args, agent, parallel, log)
  .then(result => ({ result, error: null }), e => ({ result: null, error: String(e && e.message || e) }))
  .then(out => process.stdout.write(JSON.stringify({ ...out, calls, logs })))
