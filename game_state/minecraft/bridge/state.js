// Builds a structured, LLM-friendly snapshot of the bot's world.
'use strict'

const ENTITY_RADIUS = 24
const BLOCK_RADIUS = 16

// Block names worth reporting in the "nearby resources" summary.
const NOTABLE_BLOCK = /(_log|_ore|^ancient_debris$|^crafting_table$|^furnace$|^blast_furnace$|^smoker$|^chest$|^barrel$|_bed$|^water$|^lava$|^sugar_cane$|^pumpkin$|^melon$|^wheat$|^carrots$|^potatoes$|^beetroots$|^obsidian$|^clay$|^sand$|^gravel$)/

function posObj (p) {
  return { x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) }
}

function round1 (n) { return Math.round(n * 10) / 10 }

function inventorySummary (bot) {
  const counts = {}
  for (const item of bot.inventory.items()) {
    counts[item.name] = (counts[item.name] || 0) + item.count
  }
  const freeSlots = bot.inventory.emptySlotCount()
  return { items: counts, free_slots: freeSlots }
}

function equipment (bot) {
  const slots = { hand: bot.heldItem }
  const names = ['head', 'torso', 'legs', 'feet', 'off-hand']
  for (const n of names) {
    try {
      const idx = bot.getEquipmentDestSlot(n)
      slots[n] = bot.inventory.slots[idx]
    } catch (_) { /* older versions lack off-hand */ }
  }
  const out = {}
  for (const [k, v] of Object.entries(slots)) out[k] = v ? v.name : null
  return out
}

function nearbyEntities (bot) {
  const me = bot.entity.position
  const players = []
  const hostiles = []
  const animals = []
  const items = []
  for (const e of Object.values(bot.entities)) {
    if (e === bot.entity || !e.position) continue
    const d = e.position.distanceTo(me)
    if (d > ENTITY_RADIUS) continue
    const base = { id: e.id, position: posObj(e.position), distance: round1(d) }
    if (e.type === 'player') {
      players.push({ name: e.username, ...base })
    } else if (e.type === 'hostile') {
      hostiles.push({ type: e.name, ...base })
    } else if (e.type === 'animal' || e.type === 'passive' || e.type === 'water_creature' || e.type === 'ambient') {
      animals.push({ type: e.name, ...base })
    } else if (e.name === 'item') {
      let itemName = 'item'
      try {
        const it = e.getDroppedItem()
        if (it) itemName = `${it.name} x${it.count}`
      } catch (_) {}
      items.push({ item: itemName, ...base })
    }
  }
  const byDist = (a, b) => a.distance - b.distance
  return {
    players: players.sort(byDist),
    hostile_mobs: hostiles.sort(byDist).slice(0, 10),
    animals: animals.sort(byDist).slice(0, 10),
    dropped_items: items.sort(byDist).slice(0, 10)
  }
}

function nearbyBlocks (bot) {
  const mcData = bot.registry
  const ids = []
  for (const b of mcData.blocksArray) if (NOTABLE_BLOCK.test(b.name)) ids.push(b.id)
  const positions = bot.findBlocks({ matching: ids, maxDistance: BLOCK_RADIUS, count: 3000 })
  const me = bot.entity.position
  const summary = {}
  for (const p of positions) {
    const block = bot.blockAt(p)
    if (!block) continue
    // Ignore flowing fluids, they flood the summary.
    if ((block.name === 'water' || block.name === 'lava') && block.metadata !== 0) continue
    const d = p.distanceTo(me)
    const s = summary[block.name]
    if (!s) summary[block.name] = { count: 1, nearest: posObj(p), distance: round1(d) }
    else {
      s.count++
      if (d < s.distance) { s.nearest = posObj(p); s.distance = round1(d) }
    }
  }
  return summary
}

function surroundings (bot) {
  const p = bot.entity.position
  const below = bot.blockAt(p.offset(0, -1, 0))
  let biome = null
  try {
    const b = bot.blockAt(p)
    if (b && b.biome) biome = b.biome.name || null
  } catch (_) {}
  // Sky light isn't reliably populated on newer protocol versions, so check
  // for solid cover directly above instead.
  let covered = false
  for (let dy = 2; dy <= 32; dy++) {
    const b = bot.blockAt(p.offset(0, dy, 0))
    if (b && b.boundingBox === 'block' && !b.name.endsWith('_leaves')) { covered = true; break }
  }
  return {
    standing_on: below ? below.name : null,
    biome,
    sky_visible: !covered
  }
}

function buildState (bot, extra) {
  const tod = bot.time.timeOfDay
  return {
    self: {
      username: bot.username,
      position: posObj(bot.entity.position),
      dimension: bot.game.dimension,
      health: round1(bot.health),
      food: bot.food,
      saturation: round1(bot.foodSaturation || 0),
      oxygen: bot.oxygenLevel,
      xp_level: bot.experience ? bot.experience.level : 0,
      gamemode: bot.game.gameMode,
      equipment: equipment(bot),
      on_ground: bot.entity.onGround,
      in_water: bot.entity.isInWater || false
    },
    world: {
      time_of_day: tod,
      is_day: tod < 12542 || tod > 23460,
      raining: bot.isRaining,
      difficulty: bot.game.difficulty,
      ...surroundings(bot)
    },
    inventory: inventorySummary(bot),
    nearby: {
      ...nearbyEntities(bot),
      blocks: nearbyBlocks(bot)
    },
    online_players: Object.keys(bot.players).filter((n) => n !== bot.username),
    activity: {
      current_action: extra.currentAction,
      following: extra.followTarget
    }
  }
}

module.exports = { buildState }
