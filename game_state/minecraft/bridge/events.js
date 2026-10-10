// Ring buffer of bot events with long-poll support.
'use strict'

class EventLog {
  constructor (capacity) {
    this.capacity = capacity
    this.items = []
    this.lastId = 0
    this.waiters = []
  }

  push (type, data) {
    const ev = { id: ++this.lastId, type, time: Date.now() / 1000, data }
    this.items.push(ev)
    if (this.items.length > this.capacity) this.items.shift()
    const waiters = this.waiters
    this.waiters = []
    for (const w of waiters) w()
    return ev
  }

  since (after) {
    return this.items.filter((e) => e.id > after)
  }

  waitFor (after, waitMs) {
    const now = this.since(after)
    if (now.length || waitMs <= 0) return Promise.resolve(now)
    return new Promise((resolve) => {
      let done = false
      const finish = () => {
        if (done) return
        done = true
        clearTimeout(timer)
        resolve(this.since(after))
      }
      const timer = setTimeout(finish, waitMs)
      this.waiters.push(finish)
    })
  }
}

module.exports = { EventLog }
