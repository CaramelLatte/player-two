// Dev helper: join a (offline-mode) test server as a fake human player and send
// chat lines, printing whatever the agent says back.
//
//   node scripts/fake_player.js --port 25566 --name CinnamonLatte "PlayerTwo come here" "+20" "thanks!"
//
// Arguments after the flags are chat lines; "+N" waits N seconds instead.
'use strict'
const path = require('path')
const mineflayer = require(path.join(__dirname, '../game_state/minecraft/bridge/node_modules/mineflayer'))

const argv = process.argv.slice(2)
const opts = { host: 'localhost', port: 25565, name: 'CinnamonLatte', linger: 30 }
const lines = []
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--port') opts.port = parseInt(argv[++i], 10)
  else if (argv[i] === '--host') opts.host = argv[++i]
  else if (argv[i] === '--name') opts.name = argv[++i]
  else if (argv[i] === '--linger') opts.linger = parseInt(argv[++i], 10)
  else lines.push(argv[i])
}

const bot = mineflayer.createBot({ host: opts.host, port: opts.port, username: opts.name, auth: 'offline' })
const t0 = Date.now()
const ts = () => `[${((Date.now() - t0) / 1000).toFixed(1)}s]`
bot.on('chat', (u, m) => { if (u !== bot.username) console.log(ts(), `<${u}> ${m}`) })
bot.on('kicked', (r) => console.log('kicked', r))
bot.on('error', (e) => console.log('error', e.message))
bot.once('spawn', async () => {
  console.log(ts(), `${bot.username} spawned at ${bot.entity.position.floored()}`)
  await new Promise((r) => setTimeout(r, 2000))
  for (const line of lines) {
    if (/^\+\d+$/.test(line)) { await new Promise((r) => setTimeout(r, parseInt(line.slice(1), 10) * 1000)); continue }
    console.log(ts(), `>> ${line}`)
    bot.chat(line)
    await new Promise((r) => setTimeout(r, 1500))
  }
  await new Promise((r) => setTimeout(r, opts.linger * 1000))
  bot.quit()
  process.exit(0)
})
