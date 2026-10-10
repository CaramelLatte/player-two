// Action primitives executed by the bot. Each action is
//   async (bot, args, ctx) => result
// and should call ctx.check() / ctx.race() around long awaits so it can be
// cancelled (by /stop, a pre-empting action, or the timeout).
//
// Errors are thrown as plain Error messages written for the LLM to read.
'use strict'

const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')

const { GoalNear, GoalFollow, GoalLookAtBlock, GoalInvert, GoalBlock } = goals

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// ---------------------------------------------------------------------------
// Cancellation / timeout context

class CancelledError extends Error {}

class ActionContext {
  constructor (bot, { timeoutMs }) {
    this.bot = bot
    this.cancelled = false
    this.reason = null
    this._rejectors = new Set()
    this._timer = setTimeout(() => this.cancel(`timed out after ${Math.round(timeoutMs / 1000)}s`), timeoutMs)
  }

  cancel (reason) {
    if (this.cancelled) return
    this.cancelled = true
    this.reason = reason
    try { this.bot.pathfinder.setGoal(null) } catch (_) {}
    try { this.bot.stopDigging() } catch (_) {}
    try { this.bot.clearControlStates() } catch (_) {}
    for (const reject of this._rejectors) reject(new CancelledError(`action cancelled: ${reason}`))
    this._rejectors.clear()
  }

  check () {
    if (this.cancelled) throw new CancelledError(`action cancelled: ${this.reason}`)
  }

  // Race a promise against cancellation.
  race (promise) {
    this.check()
    let rejector
    const cancelP = new Promise((resolve, reject) => { rejector = reject; this._rejectors.add(reject) })
    return Promise.race([promise, cancelP]).finally(() => this._rejectors.delete(rejector))
  }

  run (fn) { return this.race(fn()) }

  dispose () { clearTimeout(this._timer) }
}

// ---------------------------------------------------------------------------
// Helpers

function posObj (p) { return { x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) } }
function fmt (p) { const o = posObj(p); return `(${o.x}, ${o.y}, ${o.z})` }

function num (v, name) {
  const n = Number(v)
  if (!Number.isFinite(n)) throw new Error(`argument '${name}' must be a number`)
  return n
}

function normName (s) {
  return String(s || '').trim().toLowerCase().replace(/^minecraft:/, '').replace(/[\s-]+/g, '_')
}

const BLOCK_ALIASES = {
  wood: '_log', log: '_log', logs: '_log', tree: '_log', trees: '_log',
  cobblestone: ['stone', 'cobblestone'],
  cobble: ['stone', 'cobblestone'],
  dirt: ['dirt', 'grass_block'],
  iron: '_iron_ore', coal: '_coal_ore', gold: '_gold_ore', diamond: '_diamond_ore',
  diamonds: '_diamond_ore', copper: '_copper_ore', redstone: '_redstone_ore',
  lapis: '_lapis_ore', emerald: '_emerald_ore', sand: ['sand'], leaves: '_leaves'
}

// Resolve a loose block name ("iron", "oak log", "logs") to a list of block ids.
function resolveBlockIds (bot, term) {
  const reg = bot.registry
  let t = normName(term)
  const byPattern = (pred) => reg.blocksArray.filter((b) => pred(b.name)).map((b) => b.id)
  const alias = BLOCK_ALIASES[t]
  if (Array.isArray(alias)) return alias.map((n) => reg.blocksByName[n]).filter(Boolean).map((b) => b.id)
  if (typeof alias === 'string') {
    // e.g. '_iron_ore' should match iron_ore and deepslate_iron_ore
    const bare = alias.slice(1)
    return byPattern((n) => n === bare || n.endsWith(alias))
  }
  const exact = reg.blocksByName[t]
  // An exact ore name also matches its deepslate variant.
  let ids = byPattern((n) => n === t || n.endsWith('_' + t))
  if (ids.length) return ids
  if (exact) return [exact.id]
  if (t.endsWith('s')) {
    t = t.slice(0, -1)
    ids = byPattern((n) => n === t || n.endsWith('_' + t))
    if (ids.length) return ids
  }
  ids = byPattern((n) => n.includes(t))
  if (ids.length) return ids
  throw new Error(`unknown block type '${term}'`)
}

// Resolve a loose item name to candidate item definitions (best first).
function resolveItemCandidates (bot, term) {
  const reg = bot.registry
  let t = normName(term)
  if (reg.itemsByName[t]) return [reg.itemsByName[t]]
  if (t.endsWith('s') && reg.itemsByName[t.slice(0, -1)]) return [reg.itemsByName[t.slice(0, -1)]]
  if (t.endsWith('s')) t = t.slice(0, -1)
  let c = reg.itemsArray.filter((i) => i.name.endsWith('_' + t))
  if (!c.length) c = reg.itemsArray.filter((i) => i.name.includes(t))
  if (!c.length) throw new Error(`unknown item '${term}'`)
  // Prefer candidates already in the inventory.
  const have = new Set(bot.inventory.items().map((i) => i.type))
  c.sort((a, b) => (have.has(b.id) ? 1 : 0) - (have.has(a.id) ? 1 : 0))
  return c
}

// Find an item stack in the inventory by loose name.
function findInventoryItem (bot, term) {
  const t = normName(term)
  const items = bot.inventory.items()
  return items.find((i) => i.name === t) ||
    items.find((i) => i.name === t.replace(/s$/, '')) ||
    items.find((i) => i.name.endsWith('_' + t.replace(/s$/, ''))) ||
    items.find((i) => i.name.includes(t.replace(/s$/, ''))) ||
    null
}

function countItem (bot, itemId) {
  return bot.inventory.items().filter((i) => i.type === itemId).reduce((n, i) => n + i.count, 0)
}

function inventoryCounts (bot) {
  const out = {}
  for (const i of bot.inventory.items()) out[i.name] = (out[i.name] || 0) + i.count
  return out
}

function inventoryDelta (before, after) {
  const delta = {}
  for (const k of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const d = (after[k] || 0) - (before[k] || 0)
    if (d !== 0) delta[k] = d
  }
  return delta
}

function findPlayerEntity (bot, name) {
  const t = String(name || '').toLowerCase()
  const p = Object.values(bot.players).find((pl) => pl.username.toLowerCase() === t)
  if (!p) {
    const online = Object.keys(bot.players).filter((n) => n !== bot.username)
    throw new Error(`player '${name}' is not online (online: ${online.join(', ') || 'nobody'})`)
  }
  if (!p.entity) throw new Error(`player '${p.username}' is online but too far away to see; ask them for coordinates`)
  return p.entity
}

// Path to a goal. Fails fast (instead of hanging until the action timeout)
// when the pathfinder keeps reporting no path or makes no progress.
async function goto (bot, ctx, goal, opts = {}) {
  try {
    return await gotoOnce(bot, ctx, goal, opts)
  } catch (err) {
    if (err instanceof CancelledError || !/stuck/.test(err.message)) throw err
    // One recovery attempt: clear the blocks around our head and try again.
    await unstick(bot, ctx)
    return await gotoOnce(bot, ctx, goal, opts)
  }
}

async function unstick (bot, ctx) {
  const base = bot.entity.position.floored()
  const around = [[0, 2, 0], [1, 1, 0], [-1, 1, 0], [0, 1, 1], [0, 1, -1], [1, 2, 0], [-1, 2, 0], [0, 2, 1], [0, 2, -1]]
  for (const [dx, dy, dz] of around) {
    ctx.check()
    const b = bot.blockAt(base.offset(dx, dy, dz))
    if (b && b.boundingBox === 'block' && b.diggable && !isDangerousNeighbour(bot, b.position)) {
      try { await equipBestTool(bot, b); await ctx.race(bot.dig(b, true)) } catch (e) { if (e instanceof CancelledError) throw e }
    }
  }
}

// Don't open up lava (or optionally water) onto ourselves.
function isDangerousNeighbour (bot, pos, { water = true } = {}) {
  for (const [dx, dy, dz] of [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, -1]]) {
    const n = bot.blockAt(pos.offset(dx, dy, dz))
    if (n && (n.name === 'lava' || (water && n.name === 'water'))) return true
  }
  return false
}

async function gotoOnce (bot, ctx, goal, { stallMs = 20000 } = {}) {
  let noPathCount = 0
  let lastStatus = null
  let lastProgress = Date.now()
  let lastPos = bot.entity.position.clone()
  let failReject
  const failP = new Promise((resolve, reject) => { failReject = reject })
  const onUpdate = (r) => {
    lastStatus = r.status
    if (r.status === 'noPath') noPathCount++
    if (noPathCount >= 3) failReject(new Error('no path found (blocked, too far, or needs blocks to climb)'))
  }
  const stallTimer = setInterval(() => {
    const pos = bot.entity.position
    if (pos.distanceTo(lastPos) > 0.5) { lastPos = pos.clone(); lastProgress = Date.now() }
    // Digging counts as progress even if we haven't moved.
    if (bot.targetDigBlock) lastProgress = Date.now()
    if (Date.now() - lastProgress > stallMs) {
      failReject(new Error(`stuck: no movement for ${Math.round(stallMs / 1000)}s (pathfinder status: ${lastStatus || 'computing'})`))
    }
  }, 1000)
  bot.on('path_update', onUpdate)
  try {
    await ctx.race(Promise.race([bot.pathfinder.goto(goal), failP]))
  } catch (err) {
    if (err instanceof CancelledError) throw err
    try { bot.pathfinder.setGoal(null) } catch (_) {}
    throw new Error(`could not reach destination: ${err.message}`)
  } finally {
    clearInterval(stallTimer)
    bot.removeListener('path_update', onUpdate)
  }
}

function blockAt (bot, x, y, z) {
  const b = bot.blockAt(new Vec3(x, y, z))
  if (!b) throw new Error(`block at (${x}, ${y}, ${z}) is not loaded (too far away)`)
  return b
}

async function equipBestTool (bot, block) {
  let best = null
  let bestTime = block.digTime(null, false, false, false, [], [])
  for (const item of bot.inventory.items()) {
    const t = block.digTime(item.type, false, false, false, [], [])
    if (t < bestTime) { best = item; bestTime = t }
  }
  if (best) await bot.equip(best, 'hand')
}

function checkHarvestable (bot, block) {
  if (!block.harvestTools) return
  const have = bot.inventory.items().some((i) => block.harvestTools[i.type])
  if (have) return
  const names = Object.keys(block.harvestTools).map((id) => bot.registry.items[id].name)
  // Show the cheapest couple of options.
  throw new Error(`mining ${block.name} requires one of: ${names.slice(0, 3).join(', ')}${names.length > 3 ? ', ...' : ''}`)
}

async function pickupDrops (bot, ctx, near, radius = 6) {
  await sleep(400)
  for (let i = 0; i < 4; i++) {
    ctx.check()
    const drop = Object.values(bot.entities)
      .filter((e) => e.name === 'item' && e.position.distanceTo(near) <= radius)
      .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))[0]
    if (!drop) return
    try {
      await ctx.race(Promise.race([bot.pathfinder.goto(new GoalNear(drop.position.x, drop.position.y, drop.position.z, 0.5)), sleep(6000)]))
    } catch (err) {
      if (err instanceof CancelledError) throw err
    }
    bot.pathfinder.setGoal(null)
    await sleep(250)
  }
}

async function digBlock (bot, ctx, block) {
  if (!block || block.name === 'air' || block.name === 'cave_air') throw new Error('there is no block there to mine')
  if (!block.diggable || block.hardness === null || block.hardness < 0) throw new Error(`${block.name} cannot be mined`)
  checkHarvestable(bot, block)
  const pos = block.position.clone()
  if (!bot.canDigBlock(block) || bot.entity.position.distanceTo(pos.offset(0.5, 0.5, 0.5)) > 4.5) {
    await goto(bot, ctx, new GoalLookAtBlock(pos, bot.world, { reach: 4.5 }))
  }
  const fresh = bot.blockAt(pos)
  if (!fresh || fresh.name === 'air') return null
  await equipBestTool(bot, fresh)
  await ctx.race(bot.dig(fresh, true))
  return fresh.name
}

// Can a block be placed into this cell? (air, grass, flowers, snow layers... not fluids)
function isReplaceable (block) {
  if (!block) return false
  if (block.name === 'water' || block.name === 'lava') return false
  return block.boundingBox === 'empty' || block.name === 'snow'
}

// A free spot near the bot with solid ground, for placing utility blocks.
function findPlacementSpot (bot) {
  const base = bot.entity.position.floored()
  const occupied = (p) => p.equals(base) || p.equals(base.offset(0, 1, 0))
  const candidates = []
  for (let dx = -3; dx <= 3; dx++) {
    for (let dz = -3; dz <= 3; dz++) {
      for (const dy of [0, 1, -1]) {
        if (dx === 0 && dz === 0) continue
        const target = base.offset(dx, dy, dz)
        if (occupied(target)) continue
        const at = bot.blockAt(target)
        const below = bot.blockAt(target.offset(0, -1, 0))
        if (isReplaceable(at) && below && below.boundingBox === 'block') {
          candidates.push({ target, reference: below, face: new Vec3(0, 1, 0), d: Math.abs(dx) + Math.abs(dz) + Math.abs(dy) })
        }
      }
    }
  }
  candidates.sort((a, b) => a.d - b.d)
  return candidates[0] || null
}

async function placeItemAt (bot, ctx, item, target) {
  const faces = [[0, -1, 0], [0, 1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]]
  let reference = null
  let face = null
  for (const [dx, dy, dz] of faces) {
    const b = bot.blockAt(target.offset(dx, dy, dz))
    if (b && b.boundingBox === 'block') { reference = b; face = new Vec3(-dx, -dy, -dz); break }
  }
  if (!reference) throw new Error(`no solid block next to ${fmt(target)} to place against`)

  if (bot.entity.position.distanceTo(target.offset(0.5, 0.5, 0.5)) > 4) {
    await goto(bot, ctx, new GoalNear(target.x, target.y, target.z, 3))
  }
  // Don't try to place a block inside ourselves.
  const feet = bot.entity.position.floored()
  if ((feet.equals(target) || feet.offset(0, 1, 0).equals(target))) {
    await goto(bot, ctx, new GoalInvert(new GoalNear(target.x, target.y, target.z, 1.5)))
  }
  await bot.equip(item, 'hand')
  await ctx.race(bot.placeBlock(reference, face))
}

const CRAFTING_TABLE_RANGE = 32

async function ensureNearbyBlock (bot, ctx, blockName, itemName) {
  const reg = bot.registry
  const ids = [reg.blocksByName[blockName].id]
  let block = bot.findBlock({ matching: ids, maxDistance: CRAFTING_TABLE_RANGE })
  // A far-away table/furnace often means a long climb; making a new one is cheaper.
  if (block && block.position.distanceTo(bot.entity.position) > 8) {
    if (!bot.inventory.items().some((i) => i.name === itemName) && itemName === 'crafting_table') {
      const r = bot.recipesFor(reg.itemsByName.crafting_table.id, null, 1, null)[0]
      if (r) await safeCraft(bot, ctx, r, 1, null)
    }
    if (bot.inventory.items().some((i) => i.name === itemName)) block = null
  }
  if (!block) {
    const item = bot.inventory.items().find((i) => i.name === itemName)
    if (!item) return null
    const spot = findPlacementSpot(bot)
    if (!spot) throw new Error(`no room to place a ${itemName} here; move to open flat ground and try again`)
    await placeItemAt(bot, ctx, item, spot.target)
    block = bot.blockAt(spot.target)
    if (!block || block.name !== blockName) throw new Error(`failed to place ${itemName}`)
  }
  if (bot.entity.position.distanceTo(block.position) > 3.5) {
    try {
      await goto(bot, ctx, new GoalNear(block.position.x, block.position.y, block.position.z, 2))
    } catch (err) {
      if (err instanceof CancelledError) throw err
      throw new Error(`couldn't reach the ${blockName} at ${fmt(block.position)} (${err.message}). ` +
        `Get wood to craft a new one here (4 planks), or use go_to_surface first.`)
    }
  }
  return bot.blockAt(block.position)
}

function missingIngredients (bot, recipe, times) {
  const need = {}
  for (const d of recipe.delta) {
    if (d.count < 0) need[d.id] = (need[d.id] || 0) - d.count * times
  }
  const missing = []
  for (const [id, n] of Object.entries(need)) {
    const have = countItem(bot, Number(id))
    if (have < n) missing.push(`${n - have} ${bot.registry.items[id].name}`)
  }
  return missing
}

// Ask the server for an authoritative copy of the inventory. Item pickups that
// land mid-craft can desync mineflayer's local window model.
async function syncInventory (bot) {
  if (!bot._syncWindow) return
  try {
    await Promise.race([bot._syncWindow(bot.inventory), sleep(3000)])
  } catch (_) {}
}

async function safeCraft (bot, ctx, recipe, times, table) {
  await syncInventory(bot)
  try {
    await ctx.race(bot.craft(recipe, times, table))
  } catch (err) {
    if (err instanceof CancelledError) throw err
    // Recover: close the inventory (server returns grid items), resync, retry once.
    try { bot.closeWindow(bot.inventory) } catch (_) {}
    await sleep(300)
    await syncInventory(bot)
    await ctx.race(bot.craft(recipe, times, table))
  }
  await syncInventory(bot)
}

const WEAPON_ORDER = ['netherite_sword', 'diamond_sword', 'iron_sword', 'netherite_axe', 'diamond_axe', 'stone_sword', 'iron_axe', 'golden_sword', 'wooden_sword', 'stone_axe', 'golden_axe', 'wooden_axe']

async function equipBestWeapon (bot) {
  const items = bot.inventory.items()
  for (const name of WEAPON_ORDER) {
    const it = items.find((i) => i.name === name)
    if (it) { await bot.equip(it, 'hand'); return it.name }
  }
  return null
}

const BAD_FOOD = new Set(['rotten_flesh', 'spider_eye', 'poisonous_potato', 'pufferfish', 'chicken', 'suspicious_stew'])

function bestFood (bot) {
  const foods = bot.registry.foodsByName || {}
  const edible = bot.inventory.items().filter((i) => foods[i.name])
  if (!edible.length) return null
  const score = (i) => (BAD_FOOD.has(i.name) ? -100 : 0) + (foods[i.name].foodPoints || 0) + (foods[i.name].saturation || 0) / 2
  return edible.sort((a, b) => score(b) - score(a))[0]
}

function armorSlotFor (name) {
  if (/_helmet$|^turtle_helmet$|_head$|^carved_pumpkin$/.test(name)) return 'head'
  if (/_chestplate$|^elytra$/.test(name)) return 'torso'
  if (/_leggings$/.test(name)) return 'legs'
  if (/_boots$/.test(name)) return 'feet'
  if (name === 'shield') return 'off-hand'
  return 'hand'
}

// ---------------------------------------------------------------------------
// Actions

const actions = {}

actions.send_chat = async (bot, { message }) => {
  const text = String(message || '').replace(/\s+/g, ' ').trim()
  if (!text) throw new Error('message is empty')
  // Minecraft caps chat messages at 256 chars.
  const chunks = text.match(/.{1,250}(\s|$)|\S+/g) || [text]
  for (const c of chunks) { bot.chat(c.trim()); await sleep(300) }
  return 'message sent'
}

actions.move_to_position = async (bot, args, ctx) => {
  const x = num(args.x, 'x'); const y = num(args.y, 'y'); const z = num(args.z, 'z')
  const range = args.range !== undefined ? num(args.range, 'range') : 1
  await goto(bot, ctx, new GoalNear(x, y, z, range))
  return `arrived at ${fmt(bot.entity.position)}`
}

function skyVisible (bot) {
  const p = bot.entity.position
  for (let dy = 2; dy <= 64; dy++) {
    const b = bot.blockAt(p.offset(0, dy, 0))
    if (b && b.boundingBox === 'block' && !b.name.endsWith('_leaves')) return false
  }
  return true
}

function scaffoldItem (bot) {
  const names = ['cobblestone', 'dirt', 'cobbled_deepslate', 'netherrack', 'stone', 'andesite', 'diorite', 'granite', 'tuff']
  return bot.inventory.items().find((i) => names.includes(i.name)) || null
}

// Dig straight up and pillar until there's open sky above.
actions.go_to_surface = async (bot, args, ctx) => {
  const startY = Math.floor(bot.entity.position.y)
  for (let i = 0; i < 120 && !skyVisible(bot); i++) {
    ctx.check()
    const base = bot.entity.position.floored()
    // Jumping needs three clear blocks above our feet.
    for (const dy of [1, 2, 3]) {
      const b = bot.blockAt(base.offset(0, dy, 0))
      if (b && b.boundingBox === 'block') {
        if (isDangerousNeighbour(bot, b.position, { water: false })) throw new Error(`lava above at ${fmt(b.position)}; stopping for safety`)
        checkHarvestable(bot, b)
        await equipBestTool(bot, b)
        await ctx.race(bot.dig(b, true))
      }
    }
    const blocked = [1, 2, 3].some((dy) => { const b = bot.blockAt(base.offset(0, dy, 0)); return b && b.boundingBox === 'block' })
    if (blocked) { await sleep(200); continue } // something fell (gravel/sand); dig again
    const feet = bot.blockAt(base)
    const head = bot.blockAt(base.offset(0, 1, 0))
    if ((feet && feet.name === 'water') || (head && head.name === 'water') || bot.entity.isInWater) {
      // Swim up.
      bot.setControlState('jump', true)
      await sleep(400)
      continue
    }
    bot.setControlState('jump', false)
    if (!scaffoldItem(bot)) throw new Error('need blocks (cobblestone/dirt) to pillar up')
    // Let the pathfinder do the jump-and-place timing for a one-block step up.
    try {
      await gotoOnce(bot, ctx, new GoalBlock(base.x, base.y + 1, base.z), { stallMs: 8000 })
    } catch (err) {
      if (err instanceof CancelledError) throw err
    }
  }
  bot.setControlState('jump', false)
  if (!skyVisible(bot)) throw new Error('could not reach the surface')
  return `reached open sky at ${fmt(bot.entity.position)} (climbed ${Math.floor(bot.entity.position.y) - startY} blocks)`
}

actions.go_to_player = async (bot, args, ctx) => {
  const target = findPlayerEntity(bot, args.player_name)
  const dist = args.distance !== undefined ? num(args.distance, 'distance') : 2
  const p = target.position
  await goto(bot, ctx, new GoalNear(p.x, p.y, p.z, dist))
  return `reached ${target.username} at ${fmt(bot.entity.position)}`
}

actions.follow_player = async (bot, args) => {
  const target = findPlayerEntity(bot, args.player_name)
  const dist = args.distance !== undefined ? num(args.distance, 'distance') : 3
  bot.pathfinder.setGoal(new GoalFollow(target, dist), true)
  return `now following ${target.username} (keeps going until stop or another movement action)`
}

actions.stop = async (bot) => {
  bot.pathfinder.setGoal(null)
  bot.stopDigging()
  bot.clearControlStates()
  return 'stopped all movement and actions'
}

actions.mine_block = async (bot, args, ctx) => {
  const x = Math.floor(num(args.x, 'x')); const y = Math.floor(num(args.y, 'y')); const z = Math.floor(num(args.z, 'z'))
  const before = inventoryCounts(bot)
  const name = await digBlock(bot, ctx, blockAt(bot, x, y, z))
  if (!name) return 'block was already gone'
  await pickupDrops(bot, ctx, new Vec3(x, y, z))
  return { mined: name, inventory_change: inventoryDelta(before, inventoryCounts(bot)) }
}

actions.collect_block = async (bot, args, ctx) => {
  const ids = resolveBlockIds(bot, args.block_type)
  const count = Math.max(1, Math.min(64, Math.floor(args.count || 1)))
  const maxDistance = Math.min(args.max_distance || 48, 128)
  const before = inventoryCounts(bot)
  const skip = new Set()
  let mined = 0
  let failures = 0
  let lastError = null

  while (mined < count) {
    ctx.check()
    const positions = bot.findBlocks({ matching: ids, maxDistance, count: 64 })
      .filter((p) => !skip.has(p.toString()))
    if (!positions.length) break
    const me = bot.entity.position
    positions.sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
    const block = bot.blockAt(positions[0])
    try {
      const name = await digBlock(bot, ctx, block)
      if (name) {
        mined++
        failures = 0
        await pickupDrops(bot, ctx, positions[0])
      }
    } catch (err) {
      if (err instanceof CancelledError) {
        if (mined === 0) throw err
        lastError = err.message
        break
      }
      // Harvest-tool errors won't fix themselves by trying another block.
      if (/requires one of/.test(err.message)) throw err
      lastError = err.message
      skip.add(positions[0].toString())
      if (++failures >= 5) break
    }
  }

  const gained = inventoryDelta(before, inventoryCounts(bot))
  if (mined === 0) {
    const names = ids.slice(0, 4).map((id) => bot.registry.blocks[id].name).join('/')
    throw new Error(lastError ? `could not collect any ${names}: ${lastError}` : `no ${names} found within ${maxDistance} blocks`)
  }
  const res = { mined, requested: count, inventory_change: gained }
  if (mined < count) res.note = lastError ? `stopped early: ${lastError}` : 'no more found nearby'
  return res
}

actions.find_blocks = async (bot, args) => {
  const ids = resolveBlockIds(bot, args.block_type)
  const maxDistance = Math.min(args.max_distance || 64, 128)
  const count = Math.min(args.count || 5, 20)
  const me = bot.entity.position
  const found = bot.findBlocks({ matching: ids, maxDistance, count: 200 })
    .sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
    .slice(0, count)
    .map((p) => ({ block: bot.blockAt(p).name, position: posObj(p), distance: Math.round(p.distanceTo(me)) }))
  if (!found.length) return `none found within ${maxDistance} blocks`
  return found
}

actions.place_block = async (bot, args, ctx) => {
  const item = findInventoryItem(bot, args.item_name)
  if (!item) throw new Error(`no ${args.item_name} in inventory`)
  let target
  if (args.x === undefined || args.y === undefined || args.z === undefined) {
    const spot = findPlacementSpot(bot)
    if (!spot) throw new Error('no free spot next to me to place a block')
    target = spot.target
  } else {
    target = new Vec3(Math.floor(num(args.x, 'x')), Math.floor(num(args.y, 'y')), Math.floor(num(args.z, 'z')))
  }
  const existing = bot.blockAt(target)
  if (existing && !isReplaceable(existing)) throw new Error(`there is already ${existing.name} at ${fmt(target)}`)
  await placeItemAt(bot, ctx, item, target)
  return `placed ${item.name} at ${fmt(target)}`
}

actions.craft_item = async (bot, args, ctx) => {
  const quantity = Math.max(1, Math.floor(args.quantity || 1))
  const candidates = resolveItemCandidates(bot, args.item_name)
  const tableBlock = bot.findBlock({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: CRAFTING_TABLE_RANGE })

  // Pick the first candidate we can actually craft (e.g. "planks" -> oak_planks).
  let item = null
  let recipe = null
  let needsTable = false
  for (const cand of candidates.slice(0, 12)) {
    const r = bot.recipesFor(cand.id, null, 1, null)[0]
    if (r) { item = cand; recipe = r; break }
    const rt = bot.recipesFor(cand.id, null, 1, tableBlock || true)[0]
    if (rt) { item = cand; recipe = rt; needsTable = true; break }
  }

  if (!recipe) {
    const cand = candidates[0]
    const all = bot.recipesAll(cand.id, null, true)
    if (!all.length) throw new Error(`${cand.name} has no crafting recipe (it may need smelting or gathering instead)`)
    // Report against the recipe variant closest to what we already have
    // (e.g. oak planks rather than cherry planks).
    const missingFor = (r) => missingIngredients(bot, r, Math.ceil(quantity / r.result.count))
    const shortfall = (r) => missingFor(r).reduce((n, m) => n + parseInt(m, 10), 0)
    const best = all.slice().sort((a, b) => shortfall(a) - shortfall(b))[0]
    const missing = missingFor(best)
    throw new Error(`cannot craft ${cand.name}: missing ${missing.join(', ')}`)
  }

  let table = null
  if (needsTable) {
    table = await ensureNearbyBlock(bot, ctx, 'crafting_table', 'crafting_table')
    if (!table) throw new Error(`${item.name} needs a crafting table; none nearby and none in inventory (craft one from 4 planks)`)
    recipe = bot.recipesFor(item.id, null, 1, table)[0]
    if (!recipe) throw new Error(`cannot craft ${item.name} at the table`)
  }

  const before = countItem(bot, item.id)
  let times = Math.ceil(quantity / recipe.result.count)
  // Craft as many as materials allow, up to what was asked.
  while (times > 0) {
    const missing = missingIngredients(bot, recipe, times)
    if (!missing.length) break
    times--
  }
  if (times === 0) times = 1
  await safeCraft(bot, ctx, recipe, times, table)
  const made = countItem(bot, item.id) - before
  const res = { crafted: item.name, count: made }
  if (made < quantity) res.note = `only had materials for ${made} of ${quantity}`
  return res
}

const FUELS = ['coal', 'charcoal', 'coal_block', 'lava_bucket', 'blaze_rod']
const FUEL_PATTERNS = [/_planks$/, /_log$/, /^stick$/]

function findFuel (bot) {
  const items = bot.inventory.items()
  for (const f of FUELS) { const it = items.find((i) => i.name === f); if (it) return it }
  for (const p of FUEL_PATTERNS) { const it = items.find((i) => p.test(i.name)); if (it) return it }
  return null
}

actions.smelt_item = async (bot, args, ctx) => {
  const input = findInventoryItem(bot, args.item_name)
  if (!input) throw new Error(`no ${args.item_name} in inventory to smelt`)
  const quantity = Math.max(1, Math.min(Math.floor(args.quantity || 1), countItem(bot, input.type)))
  const furnaceBlock = await ensureNearbyBlock(bot, ctx, 'furnace', 'furnace')
  if (!furnaceBlock) throw new Error('no furnace nearby and none in inventory (craft one from 8 cobblestone)')

  const furnace = await ctx.race(bot.openFurnace(furnaceBlock))
  try {
    // Leftovers from an earlier run: grab finished output first.
    if (furnace.outputItem()) await furnace.takeOutput()
    const fuelNeeded = (fuel) => {
      const perItem = fuel.name === 'coal' || fuel.name === 'charcoal' ? 8 : /_planks$|_log$/.test(fuel.name) ? 1.5 : 0.5
      return Math.min(fuel.count, Math.ceil(quantity / perItem))
    }
    if (!furnace.fuelItem()) {
      const fuel = findFuel(bot)
      if (!fuel) throw new Error('no fuel in inventory (coal, charcoal, planks or logs)')
      await furnace.putFuel(fuel.type, null, fuelNeeded(fuel))
    }
    const existing = furnace.inputItem()
    if (existing && existing.type !== input.type) await furnace.takeInput()
    await furnace.putInput(input.type, null, quantity)

    // ~10s per item in a regular furnace.
    const deadline = Date.now() + quantity * 10500 + 5000
    let taken = 0
    let outName = null
    while (Date.now() < deadline && taken < quantity) {
      ctx.check()
      await sleep(1000)
      const out = furnace.outputItem()
      if (out && out.count > 0) {
        outName = out.name
        taken += out.count
        await furnace.takeOutput()
      }
      if (!furnace.inputItem() && !furnace.outputItem() && taken > 0) break
      if (!furnace.fuelItem() && furnace.inputItem() && furnace.fuelSeconds !== undefined && furnace.fuelSeconds <= 0) {
        const fuel = findFuel(bot)
        if (!fuel) break
        await furnace.putFuel(fuel.type, null, fuelNeeded(fuel))
      }
    }
    const res = { smelted: input.name, produced: outName, count: taken }
    if (taken < quantity) res.note = 'not finished yet; items left in the furnace can be collected by smelting again'
    return res
  } finally {
    furnace.close()
  }
}

actions.attack_entity = async (bot, args, ctx) => {
  let target = null
  if (args.entity_id !== undefined && args.entity_id !== null) {
    target = bot.entities[Number(args.entity_id)]
    if (!target) throw new Error(`no entity with id ${args.entity_id} nearby`)
  } else if (args.entity_type) {
    const t = normName(args.entity_type)
    target = bot.nearestEntity((e) => e !== bot.entity && e.isValid !== false &&
      (e.name === t || (e.username && e.username.toLowerCase() === t)) &&
      e.position.distanceTo(bot.entity.position) < 32)
    if (!target) throw new Error(`no ${args.entity_type} within 32 blocks`)
  } else {
    throw new Error('provide entity_id or entity_type')
  }
  if (target.type === 'player') throw new Error('refusing to attack a player')

  const weapon = await equipBestWeapon(bot)
  const label = target.name || target.username || 'entity'
  let dead = false
  let gone = false
  const onDead = (e) => { if (e.id === target.id) dead = true }
  const onGone = (e) => { if (e.id === target.id) gone = true }
  bot.on('entityDead', onDead)
  bot.on('entityGone', onGone)
  try {
    bot.pathfinder.setGoal(new GoalFollow(target, 2), true)
    let swings = 0
    while (!dead && !gone) {
      ctx.check()
      const d = target.position.distanceTo(bot.entity.position)
      if (d > 32) throw new Error(`${label} got away`)
      if (d < 3.5) {
        await bot.lookAt(target.position.offset(0, (target.height || 1) * 0.8, 0), true)
        bot.attack(target)
        swings++
      }
      await sleep(620)
    }
    if (dead || swings > 0) {
      bot.pathfinder.setGoal(null)
      try { await pickupDrops(bot, ctx, target.position.clone(), 5) } catch (err) { if (err instanceof CancelledError) throw err }
      return `killed ${label} (${swings} hits${weapon ? ' with ' + weapon : ''})`
    }
    throw new Error(`lost track of the ${label} before reaching it`)
  } finally {
    bot.removeListener('entityDead', onDead)
    bot.removeListener('entityGone', onGone)
    bot.pathfinder.setGoal(null)
  }
}

actions.equip_item = async (bot, args) => {
  const item = findInventoryItem(bot, args.item_name)
  if (!item) throw new Error(`no ${args.item_name} in inventory`)
  const dest = args.destination || armorSlotFor(item.name)
  await bot.equip(item, dest)
  return `equipped ${item.name} to ${dest}`
}

actions.eat = async (bot, args, ctx) => {
  if (bot.food >= 20) return 'not hungry (food is full)'
  const item = args.item_name ? findInventoryItem(bot, args.item_name) : bestFood(bot)
  if (!item) throw new Error(args.item_name ? `no ${args.item_name} in inventory` : 'no food in inventory')
  await bot.equip(item, 'hand')
  await ctx.race(bot.consume())
  return `ate ${item.name}; food is now ${bot.food}/20`
}

actions.give_item = async (bot, args, ctx) => {
  const item = findInventoryItem(bot, args.item_name)
  if (!item) throw new Error(`no ${args.item_name} in inventory`)
  const target = findPlayerEntity(bot, args.player_name)
  if (target.position.distanceTo(bot.entity.position) > 3) {
    const p = target.position
    await goto(bot, ctx, new GoalNear(p.x, p.y, p.z, 2))
  }
  await bot.lookAt(target.position.offset(0, 1.6, 0), true)
  const have = countItem(bot, item.type)
  const count = Math.min(have, Math.max(1, Math.floor(args.count || 1)))
  await bot.toss(item.type, null, count)
  return `gave ${count} ${item.name} to ${target.username}`
}

actions.drop_item = async (bot, args) => {
  const item = findInventoryItem(bot, args.item_name)
  if (!item) throw new Error(`no ${args.item_name} in inventory`)
  const have = countItem(bot, item.type)
  const count = args.count ? Math.min(have, Math.floor(args.count)) : have
  await bot.toss(item.type, null, count)
  return `dropped ${count} ${item.name}`
}

actions.sleep_in_bed = async (bot, args, ctx) => {
  const bed = bot.findBlock({ matching: (b) => bot.isABed(b), maxDistance: 32 })
  if (!bed) throw new Error('no bed within 32 blocks')
  await goto(bot, ctx, new GoalNear(bed.position.x, bed.position.y, bed.position.z, 2))
  try {
    await ctx.race(bot.sleep(bot.blockAt(bed.position)))
  } catch (err) {
    if (err instanceof CancelledError) throw err
    throw new Error(`could not sleep: ${err.message}`)
  }
  return 'sleeping in bed'
}

actions.look_at = async (bot, args) => {
  await bot.lookAt(new Vec3(num(args.x, 'x'), num(args.y, 'y'), num(args.z, 'z')), true)
  return 'looking'
}

// Internal helpers exposed to index.js (underscore = not callable over HTTP).
actions._bestFood = bestFood
actions._followTarget = (bot) => {
  const g = bot.pathfinder && bot.pathfinder.goal
  if (g instanceof GoalFollow && g.entity) return g.entity.username || g.entity.name || null
  return null
}

module.exports = { actions, ActionContext, CancelledError, GoalBlock }
