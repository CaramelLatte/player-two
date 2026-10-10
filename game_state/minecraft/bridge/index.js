// Player Two — Mineflayer bridge.
//
// Owns the Minecraft bot connection and exposes it to the Python core over a
// small local HTTP API:
//
//   GET  /health                  -> connection status
//   GET  /state                   -> structured game state snapshot
//   GET  /events?after=N&wait=S   -> long-poll for bot events (chat, death, ...)
//   POST /action  {name, args}    -> run one action primitive, returns result
//   POST /stop                    -> cancel whatever the bot is doing
//
// Config comes from environment variables (see config.js / README).

'use strict'

const http = require('http')
const mineflayer = require('mineflayer')
const { pathfinder, Movements } = require('mineflayer-pathfinder')

const config = require('./config')
const { EventLog } = require('./events')
const { buildState } = require('./state')
const { actions, ActionContext } = require('./actions')

const events = new EventLog(500)

let bot = null
let connected = false
let reconnectTimer = null
let currentAction = null // { name, ctx, startedAt }

function log (...args) {
  console.log(new Date().toISOString(), '[bridge]', ...args)
}

// ---------------------------------------------------------------------------
// Bot lifecycle

function createBot () {
  log(`connecting to ${config.host}:${config.port} as ${config.username} (auth=${config.auth})`)
  const opts = {
    host: config.host,
    port: config.port,
    username: config.username,
    auth: config.auth,
    profilesFolder: config.profilesFolder,
    hideErrors: false
  }
  if (config.version) opts.version = config.version
  if (config.auth === 'microsoft') {
    opts.onMsaCode = (data) => {
      log('=== MICROSOFT LOGIN REQUIRED ===')
      log(`Open ${data.verification_uri} and enter code: ${data.user_code}`)
      events.push('auth_required', { url: data.verification_uri, code: data.user_code })
    }
  }

  bot = mineflayer.createBot(opts)
  bot.loadPlugin(pathfinder)

  bot.once('spawn', () => {
    connected = true
    const movements = new Movements(bot)
    movements.allowSprinting = true
    movements.canDig = true
    movements.allowParkour = false // parkour jumps are the most common way to get stuck
    bot.pathfinder.setMovements(movements)
    bot.pathfinder.thinkTimeout = 10000
    log(`spawned at ${fmtPos(bot.entity.position)} (mc ${bot.version})`)
    events.push('spawn', { position: posObj(bot.entity.position), version: bot.version })
  })

  if (process.env.BRIDGE_DEBUG) {
    bot.on('path_reset', (reason) => log('path_reset:', reason))
    bot.on('path_update', (r) => log('path_update:', r.status, 'len', r.path.length, r.path.slice(0, 3).map((n) => `${n.x},${n.y},${n.z}${n.toPlace && n.toPlace.length ? ' place' : ''}${n.toBreak && n.toBreak.length ? ' break' : ''}`).join(' ')))
  }

  bot.on('chat', (username, message) => {
    if (username === bot.username) return
    events.push('chat', { sender: username, message })
  })

  bot.on('whisper', (username, message) => {
    if (username === bot.username) return
    events.push('whisper', { sender: username, message })
  })

  bot.on('death', () => {
    log('bot died')
    events.push('death', { position: bot.entity ? posObj(bot.entity.position) : null })
    cancelCurrent('bot died')
  })

  bot.on('respawn', () => events.push('respawn', {}))

  let lastHurt = 0
  bot.on('entityHurt', (entity) => {
    if (!bot.entity || entity.id !== bot.entity.id) return
    const now = Date.now()
    if (now - lastHurt < 5000) return // rate limit
    lastHurt = now
    events.push('hurt', { health: bot.health })
  })

  bot.on('health', () => {
    if (config.autoEat && bot.food <= 14 && !eatingReflex) maybeAutoEat()
  })

  bot.on('playerJoined', (p) => {
    if (connected && p.username !== bot.username) events.push('player_joined', { player: p.username })
  })
  bot.on('playerLeft', (p) => {
    if (p.username !== bot.username) events.push('player_left', { player: p.username })
  })

  bot.on('kicked', (reason) => {
    log('kicked:', typeof reason === 'string' ? reason : JSON.stringify(reason))
    events.push('kicked', { reason: typeof reason === 'string' ? reason : JSON.stringify(reason) })
  })

  bot.on('error', (err) => {
    log('error:', err.message)
    events.push('error', { message: err.message })
  })

  bot.on('end', (reason) => {
    log('disconnected:', reason)
    connected = false
    cancelCurrent('disconnected')
    events.push('disconnected', { reason: String(reason) })
    scheduleReconnect()
  })
}

function scheduleReconnect () {
  if (reconnectTimer) return
  log(`reconnecting in ${config.reconnectDelayMs / 1000}s`)
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    try {
      createBot()
    } catch (err) {
      log('reconnect failed:', err.message)
      scheduleReconnect()
    }
  }, config.reconnectDelayMs)
}

let eatingReflex = false
async function maybeAutoEat () {
  if (currentAction) return // don't fight the agent for the hands
  const food = actions._bestFood(bot)
  if (!food) return
  eatingReflex = true
  try {
    await bot.equip(food, 'hand')
    await bot.consume()
    events.push('auto_ate', { item: food.name, food: bot.food })
  } catch (_) {
    // ignore — eating can fail if interrupted, it will retry on next health tick
  } finally {
    eatingReflex = false
  }
}

function cancelCurrent (reason) {
  if (!currentAction) return
  currentAction.ctx.cancel(reason)
}

function stopEverything (reason) {
  cancelCurrent(reason)
  if (!bot) return
  try { bot.pathfinder.stop() } catch (_) {}
  try { bot.pathfinder.setGoal(null) } catch (_) {}
  try { bot.stopDigging() } catch (_) {}
  try { bot.clearControlStates() } catch (_) {}
}

// ---------------------------------------------------------------------------
// HTTP API

function sendJson (res, status, body) {
  const data = JSON.stringify(body)
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) })
  res.end(data)
}

function readBody (req) {
  return new Promise((resolve, reject) => {
    let data = ''
    req.on('data', (c) => { data += c })
    req.on('end', () => {
      if (!data) return resolve({})
      try { resolve(JSON.parse(data)) } catch (e) { reject(new Error('invalid JSON body')) }
    })
    req.on('error', reject)
  })
}

async function runAction (name, args) {
  const fn = actions[name]
  if (!fn || name.startsWith('_')) return { ok: false, error: `unknown action '${name}'` }
  if (!connected || !bot || !bot.entity) return { ok: false, error: 'bot is not connected to the server' }

  // One action at a time: a new action pre-empts the previous one.
  if (currentAction) stopEverything(`pre-empted by ${name}`)

  const ctx = new ActionContext(bot, { timeoutMs: (args && args.timeout_s ? args.timeout_s * 1000 : config.actionTimeoutMs) })
  const entry = { name, ctx, startedAt: Date.now() }
  currentAction = entry
  try {
    const result = await ctx.run(() => fn(bot, args || {}, ctx))
    return { ok: true, result }
  } catch (err) {
    return { ok: false, error: err && err.message ? err.message : String(err) }
  } finally {
    if (currentAction === entry) currentAction = null
    ctx.dispose()
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost')
  try {
    if (req.method === 'GET' && url.pathname === '/health') {
      return sendJson(res, 200, {
        connected,
        username: bot ? bot.username : null,
        version: bot ? bot.version : null,
        current_action: currentAction ? currentAction.name : null
      })
    }

    if (req.method === 'GET' && url.pathname === '/state') {
      if (!connected || !bot || !bot.entity) return sendJson(res, 503, { error: 'bot not connected' })
      const state = buildState(bot, {
        currentAction: currentAction ? currentAction.name : null,
        followTarget: actions._followTarget(bot)
      })
      return sendJson(res, 200, state)
    }

    if (req.method === 'GET' && url.pathname === '/events') {
      const after = parseInt(url.searchParams.get('after') || '0', 10)
      const wait = Math.min(parseFloat(url.searchParams.get('wait') || '0'), 60)
      const list = await events.waitFor(after, wait * 1000)
      return sendJson(res, 200, { events: list, last_id: events.lastId })
    }

    if (req.method === 'POST' && url.pathname === '/action') {
      const body = await readBody(req)
      const result = await runAction(body.name, body.args)
      return sendJson(res, 200, result)
    }

    if (req.method === 'POST' && url.pathname === '/stop') {
      const was = currentAction ? currentAction.name : null
      stopEverything('stop requested')
      return sendJson(res, 200, { ok: true, stopped: was })
    }

    sendJson(res, 404, { error: 'not found' })
  } catch (err) {
    sendJson(res, 500, { error: err.message })
  }
})

function posObj (p) { return { x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) } }
function fmtPos (p) { const o = posObj(p); return `${o.x},${o.y},${o.z}` }

server.listen(config.bridgePort, config.bridgeHost, () => {
  log(`HTTP API listening on http://${config.bridgeHost}:${config.bridgePort}`)
  createBot()
})

process.on('SIGINT', () => { log('shutting down'); try { bot && bot.quit() } catch (_) {} process.exit(0) })
process.on('SIGTERM', () => { try { bot && bot.quit() } catch (_) {} process.exit(0) })
process.on('unhandledRejection', (err) => log('unhandled rejection:', err && err.message ? err.message : err))
