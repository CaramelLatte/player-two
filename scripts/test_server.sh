#!/usr/bin/env bash
# Throwaway offline-mode Minecraft server for development (port 25566).
#   scripts/test_server.sh up      # start (first run downloads the server)
#   scripts/test_server.sh down    # stop and delete it
#   scripts/test_server.sh rcon "give PlayerTwo oak_log 8"
set -euo pipefail
NAME=p2_test_mc
DATA="${P2_TEST_DATA:-$HOME/.cache/player-two/test-mc}"
case "${1:-}" in
  up)
    mkdir -p "$DATA"
    docker run -d --name "$NAME" -p 25566:25565 \
      -e EULA=TRUE -e VERSION=26.1 -e ONLINE_MODE=FALSE -e ENFORCE_SECURE_PROFILE=FALSE \
      -e DIFFICULTY=peaceful -e MODE=survival -e SPAWN_PROTECTION=0 \
      -e UID="$(id -u)" -e GID="$(id -g)" -v "$DATA:/data" itzg/minecraft-server:latest
    echo "started $NAME on localhost:25566 (data: $DATA)" ;;
  down) docker rm -f "$NAME" ;;
  rcon) shift; docker exec "$NAME" rcon-cli "$@" ;;
  *) echo "usage: $0 up|down|rcon <command>"; exit 1 ;;
esac
