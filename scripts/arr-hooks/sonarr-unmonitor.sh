#!/bin/bash
# Sonarr Custom Script connection (Settings -> Connect -> Custom Script),
# triggered On Import and On Upgrade. Runs INSIDE the sonarr container --
# registered automatically by `configure_auto_unmonitor()` in setup/configure.py.
#
# Goal: once a SEASON is fully downloaded and nothing more is coming for it,
# stop Sonarr actively monitoring/re-searching it. A season still airing
# (there's a next episode date) is left completely alone -- this is why the
# check is per-season, not per-series: an ongoing show like South Park has
# old, finished seasons that should stop being monitored right alongside a
# current season that hasn't finished airing yet and must keep being watched.
#
# Sonarr's season-level `nextAiring` (in /api/v3/series's `seasons[].statistics`)
# is exactly this signal -- null means "nothing scheduled for this season",
# which is only true once the season has actually wrapped. Verified live
# against South Park: its finished season 1 and season 28 both show
# nextAiring=null at 100% complete; its *current* season 29 -- also 100% of
# known episodes downloaded -- still shows a nextAiring date, and is
# correctly left alone by this script.
set -euo pipefail

[ "${sonarr_eventtype:-}" = "Download" ] || exit 0

series_id="${sonarr_series_id:-}"
season_number="${sonarr_episodefile_seasonnumber:-}"
[ -n "$series_id" ] && [ -n "$season_number" ] || exit 0

api_key="$(grep -o '<ApiKey>[^<]*</ApiKey>' /config/config.xml | sed 's/<[^>]*>//g')"
base="http://localhost:8989/api/v3"
auth=(-H "X-Api-Key: $api_key")

series="$(curl -s "${auth[@]}" "$base/series/$series_id")"

season="$(echo "$series" | jq --argjson n "$season_number" '.seasons[] | select(.seasonNumber == $n)')"
[ -n "$season" ] || exit 0

monitored="$(echo "$season" | jq -r '.monitored')"
episode_count="$(echo "$season" | jq -r '.statistics.episodeCount')"
percent_complete="$(echo "$season" | jq -r '.statistics.percentOfEpisodes')"
next_airing="$(echo "$season" | jq -r '.statistics.nextAiring // empty')"

if [ "$monitored" = "true" ] && [ "$episode_count" -gt 0 ] \
   && [ "$(echo "$percent_complete" | cut -d. -f1)" -eq 100 ] \
   && [ -z "$next_airing" ]; then
  updated="$(echo "$series" | jq --argjson n "$season_number" \
    '.seasons |= map(if .seasonNumber == $n then .monitored = false else . end)')"
  curl -s -o /dev/null -w '' -X PUT "${auth[@]}" -H "Content-Type: application/json" \
    -d "$updated" "$base/series/$series_id"
fi
