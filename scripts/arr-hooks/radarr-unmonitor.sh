#!/bin/bash
# Radarr Custom Script connection (Settings -> Connect -> Custom Script),
# triggered On Import and On Upgrade. Runs INSIDE the radarr container --
# registered automatically by `configure_auto_unmonitor()` in setup/configure.py.
#
# A movie has no "still airing" concept the way a TV season does -- once the
# file exists, there is nothing left to wait for, so this is a straight
# hasFile-and-monitored check, no season-style completeness logic needed.
set -euo pipefail

[ "${radarr_eventtype:-}" = "Download" ] || exit 0

movie_id="${radarr_movie_id:-}"
[ -n "$movie_id" ] || exit 0

api_key="$(grep -o '<ApiKey>[^<]*</ApiKey>' /config/config.xml | sed 's/<[^>]*>//g')"
base="http://localhost:7878/api/v3"
auth=(-H "X-Api-Key: $api_key")

movie="$(curl -s "${auth[@]}" "$base/movie/$movie_id")"
monitored="$(echo "$movie" | jq -r '.monitored')"
has_file="$(echo "$movie" | jq -r '.hasFile')"

if [ "$monitored" = "true" ] && [ "$has_file" = "true" ]; then
  updated="$(echo "$movie" | jq '.monitored = false')"
  curl -s -o /dev/null -w '' -X PUT "${auth[@]}" -H "Content-Type: application/json" \
    -d "$updated" "$base/movie/$movie_id"
fi
