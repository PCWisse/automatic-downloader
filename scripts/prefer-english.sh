#!/usr/bin/env bash
# Make English the default track in Matroska files that ship with a foreign
# dub flagged as default (common with ITA / MULTi scene releases). It only
# rewrites header flags -- no re-mux, no quality loss, a fraction of a second
# per file. Files with no English audio track are left untouched.
#
# Usage:
#   scripts/prefer-english.sh "/mnt/media/library/tv/FROM"     # folder, recursive
#   scripts/prefer-english.sh /path/to/one.mkv                  # single file
#   PREF_LANG=ger scripts/prefer-english.sh <path>             # prefer another language
#
# Needs:  sudo apt install mkvtoolnix ffmpeg
set -uo pipefail

PREF="${PREF_LANG:-eng}"
target="${1:?usage: prefer-english.sh <file-or-folder>}"

for bin in ffprobe mkvpropedit; do
  command -v "$bin" >/dev/null || { echo "missing $bin -- sudo apt install mkvtoolnix ffmpeg" >&2; exit 1; }
done

fix_one() {
  local f="$1"

  # ffprobe reads even the odd files mkvmerge -J chokes on. One row per audio
  # track, in file order (so row 1 -> mkvpropedit track:a1, row 2 -> a2, ...):
  #   <lang> <is_default>
  local info
  info=$(ffprobe -v error -select_streams a \
           -show_entries stream_tags=language:stream_disposition=default \
           -of csv=p=0 "$f" 2>/dev/null) || { echo "skip (unreadable): ${f##*/}"; return; }
  [ -n "$info" ] || { echo "skip (no audio): ${f##*/}"; return; }

  local n=0 want="" a_lang a_def
  local -a langs=() defs=()
  while IFS=, read -r a_def a_lang _; do
    n=$((n+1)); langs[n]="${a_lang:-und}"; defs[n]="${a_def:-0}"
    [ -z "$want" ] && [ "${a_lang:-}" = "$PREF" ] && want=$n
  done <<< "$info"

  # one audio track -> every player uses it regardless of the flag; leave it be
  [ "$n" -le 1 ] && { echo "ok (single track): ${f##*/}"; return; }
  [ -n "$want" ] || { echo "skip (no $PREF audio): ${f##*/}"; return; }

  # already correct?
  local ok=1 i
  for ((i=1;i<=n;i++)); do
    if [ "$i" = "$want" ]; then [ "${defs[i]}" = 1 ] || ok=0; else [ "${defs[i]}" = 0 ] || ok=0; fi
  done

  local -a args=()
  if [ "$ok" != 1 ]; then
    for ((i=1;i<=n;i++)); do
      args+=(--edit "track:a$i" --set "flag-default=$([ "$i" = "$want" ] && echo 1 || echo 0)")
    done
  fi

  # clear default/forced on any non-English subtitle track -- the foreign
  # "forced" sub is what burns dub captions onto an English viewing.
  local s=0 s_lang s_def s_for
  while IFS=, read -r s_def s_for s_lang _; do
    s=$((s+1))
    if [ "${s_lang:-und}" != "$PREF" ] && { [ "${s_def:-0}" = 1 ] || [ "${s_for:-0}" = 1 ]; }; then
      args+=(--edit "track:s$s" --set flag-default=0 --set flag-forced=0)
    fi
  done < <(ffprobe -v error -select_streams s \
             -show_entries stream_tags=language:stream_disposition=default,forced \
             -of csv=p=0 "$f" 2>/dev/null)

  [ "${#args[@]}" -gt 0 ] || { echo "ok already: ${f##*/}"; return; }

  if mkvpropedit "$f" "${args[@]}" >/dev/null 2>&1; then
    echo "fixed -> $PREF default: ${f##*/}"
  else
    echo "FAILED: ${f##*/}"
  fi
}

if [ -d "$target" ]; then
  find "$target" -type f -name '*.mkv' -print0 | while IFS= read -r -d '' f; do fix_one "$f"; done
else
  fix_one "$target"
fi
