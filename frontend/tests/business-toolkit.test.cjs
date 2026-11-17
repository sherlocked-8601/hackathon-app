const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const Module = require('node:module')
const { buildSync } = require('esbuild')
const compiled = new Module(path.join(__dirname, '../business-toolkit-test.cjs'), module)
compiled.filename = path.join(__dirname, '../business-toolkit-test.cjs')
compiled.paths = Module._nodeModulePaths(path.join(__dirname, '..'))
compiled._compile(buildSync({ entryPoints: [path.join(__dirname, '../src/components/BusinessToolkit.jsx')], bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react'], loader: { '.css': 'empty' } }).outputFiles[0].text, compiled.filename)
const { businessToolkitGroups, tradeSpecificCount } = compiled.exports
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../../backend/data/catalog.json'), 'utf8'))
const checkedTools = catalog.tools.map(tool => ({ ...tool, checked_capabilities: tool.capabilities.filter(capability => capability.access?.status === 'present').map(capability => capability.capability) }))
const toolIds = groups => [...new Set(groups.flatMap(group => group.tools.map(entry => entry.tool.id)))]

test('business overview includes supported examples without mixing in another industry', () => {
  const auto = toolIds(businessToolkitGroups(checkedTools, catalog.categories, 'auto'))
  const hvac = toolIds(businessToolkitGroups(checkedTools, catalog.categories, 'hvac'))
  assert.ok(auto.includes('quickbooks_online'))
  assert.ok(auto.includes('google_business_profile'))
  assert.ok(auto.includes('nicejob'))
  assert.equal(auto.includes('jobber'), false)
  assert.ok(hvac.includes('jobber'))
})

test('unknown businesses receive general-purpose examples, with pending and unchecked facts excluded', () => {
  const groups = businessToolkitGroups(checkedTools, catalog.categories, null)
  assert.ok(groups.length)
  for (const group of groups) for (const { tool } of group.tools) assert.equal(tool.applicability, 'cross_industry')
  const withoutEvidence = checkedTools.map(tool => ({ ...tool, capabilities: tool.capabilities.map(capability => ({ ...capability, access: { status: 'unknown' } })) }))
  assert.deepEqual(businessToolkitGroups(withoutEvidence, catalog.categories, 'auto'), [])
})

test('a product-wide verified flag never puts a tool in the toolkit without a task-scoped check', () => {
  assert.deepEqual(businessToolkitGroups(catalog.tools.map(tool => ({ ...tool, status: 'verified' })), catalog.categories, 'auto'), [])
  const onlyOne = checkedTools.map(tool => ({ ...tool, checked_capabilities: tool.id === 'quickbooks_online' ? tool.checked_capabilities : [] }))
  assert.deepEqual(toolIds(businessToolkitGroups(onlyOne, catalog.categories, 'auto')), ['quickbooks_online'])
})

test('when only a general-purpose tool is still checked, every business gets the same list and it is flagged as general only', () => {
  const live = checkedTools.map(tool => ({ ...tool, checked_capabilities: tool.id === 'quickbooks_online' ? tool.checked_capabilities : [] }))
  const gym = businessToolkitGroups(live, catalog.categories, 'fitness')
  const plumbing = businessToolkitGroups(live, catalog.categories, 'plumbing')
  assert.deepEqual(toolIds(gym), toolIds(plumbing))
  assert.equal(tradeSpecificCount(gym), 0)
  assert.equal(tradeSpecificCount(plumbing), 0)
  assert.ok(tradeSpecificCount(businessToolkitGroups(checkedTools, catalog.categories, 'hvac')) > 0)
})

test('a feature-checked trade tool reaches the toolkit with its open items labelled, and Gym -> Plumbing -> Gym stays stable', () => {
  const live = checkedTools.map(tool => ({ ...tool, checked_capabilities: tool.id === 'quickbooks_online' ? tool.checked_capabilities : [] }))
    .map(tool => tool.id === 'jobber' ? { ...tool, verification_state: 'refresh_due', feature_checked: { job_scheduling: ['price', 'plan'] } } : tool)
  const gym1 = businessToolkitGroups(live, catalog.categories, 'fitness')
  const plumbing = businessToolkitGroups(live, catalog.categories, 'plumbing')
  const gym2 = businessToolkitGroups(live, catalog.categories, 'fitness')
  assert.deepEqual(gym1, gym2)
  assert.equal(toolIds(gym1).includes('jobber'), false)
  assert.ok(toolIds(plumbing).includes('jobber'))
  assert.equal(tradeSpecificCount(plumbing), 1)
  const jobber = plumbing.flatMap(group => group.tools).find(entry => entry.tool.id === 'jobber')
  assert.deepEqual(jobber.purposes, [])
  assert.equal(jobber.qualified.length, 1)
  assert.equal(jobber.qualified[0].note, 'Feature verified; price requires vendor confirmation. Confirm which plan includes this feature.')
  assert.deepEqual(jobber.tool.checked_capabilities, [])
})

test('a refresh-due tool with no fresh feature check stays out', () => {
  const stale = checkedTools.map(tool => tool.id === 'jobber' ? { ...tool, verification_state: 'refresh_due' } : { ...tool, checked_capabilities: [] })
  assert.equal(toolIds(businessToolkitGroups(stale, catalog.categories, 'plumbing')).includes('jobber'), false)
})
