// Bridge configuration, read from environment variables. The Python core sets
// these from config/games/minecraft.yaml when it launches the bridge.
'use strict'

const path = require('path')

const env = process.env

module.exports = {
  host: env.MC_HOST || 'localhost',
  port: parseInt(env.MC_PORT || '25565', 10),
  username: env.MC_USERNAME || 'PlayerTwo',
  auth: env.MC_AUTH || 'offline', // 'offline' | 'microsoft'
  version: env.MC_VERSION || null, // null = auto-detect from server
  profilesFolder: env.MC_PROFILES_DIR || path.join(__dirname, '.auth'),
  bridgeHost: env.BRIDGE_HOST || '127.0.0.1',
  bridgePort: parseInt(env.BRIDGE_PORT || '3001', 10),
  actionTimeoutMs: parseInt(env.ACTION_TIMEOUT_MS || '120000', 10),
  reconnectDelayMs: parseInt(env.RECONNECT_DELAY_MS || '10000', 10),
  autoEat: (env.AUTO_EAT || 'true') === 'true'
}
