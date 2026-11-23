#!/usr/bin/env bash
# Batch run of many volumes in a tmux session, year by year.
#
#   scripts/batch/pjb_batch.sh plan   SCAN_ROOT   # dry run: what would be processed
#   scripts/batch/pjb_batch.sh start  SCAN_ROOT   # start the queue in tmux session "pjb_batch"
#   scripts/batch/pjb_batch.sh status             # done / failed / current volume
#   scripts/batch/pjb_batch.sh attach             # watch it (leave with Ctrl-b d)
#   scripts/batch/pjb_batch.sh stop [--now]       # after the current volume / immediately
#
# SCAN_ROOT holds one subfolder per volume with the page images (see plan.py).
# For every volume: write config -> wait for vLLM -> pjb-pipeline run (up to 3
# attempts, OCR is resumable) -> check_reading_order -> add-volume to the wiki ->
# commit + push the wiki and the config to branch $PJB_BRANCH in both repos.
# Finished volumes get an OK line in $PJB_BATCH_HOME/done.txt and are skipped
# on a restart; FAIL volumes are tried again.
#
# Environment (defaults):
#   PJB_BRANCH=batch-run-2026-11     run branch in both repos (created from main)
#   PJB_BATCH_HOME=~/pjb_batch       logs/, done.txt, queue.log
#   PJB_ORDER=asc                    asc = oldest year first, desc = newest first
#   PJB_VLLM_URL=http://localhost:8000/v1
#   PJB_MIN_FREE_GB=60               pause while less disk space is free
#   PJB_FORCE=0                      1 = re-run volumes that are already in the wiki
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
WIKI=${PJB_WIKI:-$REPO/../Passauer_Jahrbuecher_Wiki}
WIKI=$(cd "$WIKI" && pwd)
PY=$REPO/.venv/bin/python
PLAN="$PY $REPO/scripts/batch/plan.py"
BRANCH=${PJB_BRANCH:-batch-run-2026-11}
HOME_DIR=${PJB_BATCH_HOME:-$HOME/pjb_batch}
ORDER=${PJB_ORDER:-asc}
VLLM=${PJB_VLLM_URL:-http://localhost:8000/v1}
MIN_FREE_GB=${PJB_MIN_FREE_GB:-60}
FORCE=${PJB_FORCE:-0}
SESSION=pjb_batch
DONE=$HOME_DIR/done.txt
LOGS=$HOME_DIR/logs
mkdir -p "$LOGS"; touch "$DONE"

log() { echo "$(date '+%F %T') $*" | tee -a "$HOME_DIR/queue.log"; }

# ---------------------------------------------------------------- git
ensure_branch() {  # $1 = repo dir
  local r=$1
  git -C "$r" fetch -q origin || log "WARN fetch failed in $r"
  if [ "$(git -C "$r" branch --show-current)" != "$BRANCH" ]; then
    if ! git -C "$r" diff --quiet || ! git -C "$r" diff --cached --quiet; then
      log "ERROR $r has uncommitted changes to tracked files; not switching to $BRANCH"
      return 1
    fi
    if git -C "$r" rev-parse -q --verify "refs/heads/$BRANCH" >/dev/null; then
      git -C "$r" checkout -q "$BRANCH"
    elif git -C "$r" rev-parse -q --verify "refs/remotes/origin/$BRANCH" >/dev/null; then
      git -C "$r" checkout -q -b "$BRANCH" "origin/$BRANCH"
    else
      git -C "$r" checkout -q -b "$BRANCH" origin/main
    fi
  fi
  if git -C "$r" rev-parse -q --verify "refs/remotes/origin/$BRANCH" >/dev/null; then
    git -C "$r" pull -q --rebase origin "$BRANCH" || { log "ERROR pull --rebase failed in $r"; return 1; }
  fi
}

push() {  # $1 = repo dir; 3 tries, a failed push is caught up by the next one
  local i
  for i in 1 2 3; do
    git -C "$1" push -q -u origin "$BRANCH" 2>>"$HOME_DIR/queue.log" && return 0
    sleep 60
  done
  log "WARN push failed in $1 (commit is local, next push takes it along)"
  return 1
}

# ---------------------------------------------------------------- checks
wait_vllm() {
  local n=0
  until curl -sf -m 10 "$VLLM/models" >/dev/null; do
    [ $((n % 10)) -eq 0 ] && log "waiting for vLLM at $VLLM"
    n=$((n + 1)); sleep 60
    [ -e "$HOME_DIR/STOP" ] && return 1
  done
}

wait_disk() {
  while [ "$(df -BG --output=avail "$REPO" | tail -1 | tr -dc 0-9)" -lt "$MIN_FREE_GB" ]; do
    log "less than ${MIN_FREE_GB} GB free, waiting"; sleep 600
    [ -e "$HOME_DIR/STOP" ] && return 1
  done
}

ocr_complete() {  # $1 = output dir; prints "ok/total", exit 0 when every page has OCR
  "$PY" - "$1" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
pages = sorted((out / "pages").glob("page_*.png"))
ok = 0
for p in pages:
    try:
        ok += bool(json.loads((out / "interim" / (p.stem + ".json")).read_text()).get("raw"))
    except Exception:
        pass
print(f"{ok}/{len(pages)}")
sys.exit(0 if pages and ok == len(pages) else 1)
PY
}

# ---------------------------------------------------------------- one volume
process() {  # tag year vol roman n source
  local tag=$1 year=$2 vol=$3 roman=$4 n=$5 src=$6
  local cfg=configs/$tag-gpubox.yaml out=$REPO/output/$tag vlog=$LOGS/$tag.log
  log "== $tag ($roman, $year): $n images from $src"

  wait_disk || return 1
  ensure_branch "$REPO" && ensure_branch "$WIKI" || return 1

  if [ "$FORCE" != 1 ] && [ -e "$WIKI/volumes/$tag.md" ] && ! grep -q "^OK	$tag	" "$DONE"; then
    log "SKIP $tag is already in the wiki (PJB_FORCE=1 to redo)"
    printf 'SKIP\t%s\t%s\talready in wiki\n' "$tag" "$(date +%F_%T)" >>"$DONE"
    return 0
  fi

  (cd "$REPO" && $PLAN config "$SCAN_ROOT" "$year" "$cfg") || { log "ERROR config $tag"; return 1; }

  # Up to 3 failed attempts. A run the watchdog stops because vLLM went away
  # does not count (the OCR cache keeps every finished page), at most 20 of those.
  local attempt=0 stopped=0 status rc complete pid down
  while [ $attempt -lt 3 ] && [ $stopped -lt 20 ]; do
    wait_vllm || return 1
    log "run $tag (failed so far: $attempt, log: $vlog)"
    ( cd "$REPO" && exec "$REPO/.venv/bin/pjb-pipeline" run "$cfg" --vllm-url "$VLLM" ) >>"$vlog" 2>&1 &
    pid=$! down=0
    while kill -0 $pid 2>/dev/null; do
      sleep 30
      if curl -sf -m 10 "$VLLM/models" >/dev/null; then down=0; else down=$((down + 1)); fi
      if [ $down -ge 10 ]; then
        log "vLLM unreachable for 5 min, stopping the run of $tag (resumes from cache)"
        kill $pid; sleep 10; kill -9 $pid 2>/dev/null
      fi
    done
    wait $pid; rc=$?
    status=$(ocr_complete "$out"); complete=$?
    log "run $tag: rc=$rc, OCR pages $status"
    [ $rc -eq 0 ] && [ $complete -eq 0 ] && break
    if [ $down -ge 10 ]; then stopped=$((stopped + 1)); continue; fi
    attempt=$((attempt + 1))
    [ $attempt -lt 3 ] && sleep "${PJB_RETRY_SLEEP:-300}"
  done
  if [ $rc -ne 0 ] || [ $complete -ne 0 ]; then
    printf 'FAIL\t%s\t%s\trc=%s\tocr=%s\n' "$tag" "$(date +%F_%T)" "$rc" "$status" >>"$DONE"
    return 2
  fi

  # audit (runbook step 6): up to ~15 of each is normal
  (cd "$REPO" && "$PY" scripts/check_reading_order.py "output/$tag") >"$LOGS/$tag.check.txt" 2>&1
  local hyph cont warn=""
  hyph=$(grep -oP 'Broken hyphenation joins: \K\d+' "$LOGS/$tag.check.txt" || echo "?")
  cont=$(grep -oP 'Stranded continuations[^:]*: \K\d+' "$LOGS/$tag.check.txt" || echo "?")
  if [[ $hyph =~ ^[0-9]+$ && $cont =~ ^[0-9]+$ ]]; then
    { [ "$hyph" -gt 15 ] || [ "$cont" -gt 15 ]; } && warn="CHECK: above the usual 15"
  else
    warn="CHECK: check_reading_order failed"
  fi
  [ -n "$warn" ] && log "WARN $tag $warn (hyph=$hyph cont=$cont)"
  local msg="Add Vol $roman ($year)

Pages: ${status#*/}. Broken hyphenation joins: $hyph. Stranded continuations: $cont.${warn:+
$warn}"

  # wiki (runbook step 7)
  (cd "$REPO" && "$REPO/.venv/bin/pjb-pipeline" add-volume "$WIKI" "output/$tag/") >>"$vlog" 2>&1 \
    || { log "ERROR add-volume $tag"; printf 'FAIL\t%s\t%s\tadd-volume\n' "$tag" "$(date +%F_%T)" >>"$DONE"; return 2; }
  git -C "$WIKI" add -A
  git -C "$WIKI" diff --cached --quiet || git -C "$WIKI" commit -q -m "$msg"
  local pw=yes pp=yes
  push "$WIKI" || pw=no

  # config (runbook step 8)
  git -C "$REPO" add "$cfg"
  git -C "$REPO" diff --cached --quiet || git -C "$REPO" commit -q -m "Add config for Vol $roman ($year)"
  push "$REPO" || pp=no

  printf 'OK\t%s\t%s\tpages=%s\thyph=%s\tcont=%s\twiki=%s\tpipe=%s\tpushed=%s/%s%s\n' \
    "$tag" "$(date +%F_%T)" "${status#*/}" "$hyph" "$cont" \
    "$(git -C "$WIKI" rev-parse --short HEAD)" "$(git -C "$REPO" rev-parse --short HEAD)" \
    "$pw" "$pp" "${warn:+	$warn}" >>"$DONE"
  log "OK $tag"
}

loop() {
  rm -f "$HOME_DIR/STOP"
  local plan; plan=$(cd "$REPO" && $PLAN list "$SCAN_ROOT" --order "$ORDER") || { log "ERROR plan failed"; return 1; }
  log "queue start: $(echo "$plan" | wc -l) volumes in $SCAN_ROOT, branch $BRANCH"
  local fails=0 tag year vol roman n src rest
  while IFS=$'\t' read -r tag year vol roman n src rest; do
    [ -e "$HOME_DIR/STOP" ] && { log "STOP file found, ending"; break; }
    grep -qP "^(OK|SKIP)\t$tag\t" "$DONE" && continue
    echo "$tag" >"$HOME_DIR/current"
    process "$tag" "$year" "$vol" "$roman" "$n" "$src"
    case $? in
      0) fails=0 ;;
      1) log "stopped while preparing $tag"; break ;;
      *) fails=$((fails + 1)); log "FAIL $tag ($fails in a row)"
         [ $fails -ge 3 ] && { log "3 failures in a row, stopping"; break; } ;;
    esac
  done <<<"$plan"
  rm -f "$HOME_DIR/current"
  log "queue end"
}

# ---------------------------------------------------------------- commands
cmd=${1:-status}
case $cmd in
  plan)
    SCAN_ROOT=$(realpath "${2:?scan root}")
    (cd "$REPO" && $PLAN list "$SCAN_ROOT" --order "$ORDER") | while IFS=$'\t' read -r tag year vol roman n src title sub; do
      st=$(grep -P "^\w+\t$tag\t" "$DONE" | tail -1 | cut -f1)
      [ -z "$st" ] && [ -e "$WIKI/volumes/$tag.md" ] && st="in wiki"
      printf '%-14s %-8s %4s images  %-22s %s\n' "$tag" "$roman" "$n" "${st:-todo}" "$(basename "$src")"
    done ;;
  start)
    SCAN_ROOT=$(realpath "${2:?scan root}")
    tmux has-session -t "=$SESSION" 2>/dev/null && { echo "session $SESSION is already running"; exit 1; }
    tmux new-session -d -s "$SESSION" \
      -e PJB_BRANCH="$BRANCH" -e PJB_BATCH_HOME="$HOME_DIR" -e PJB_ORDER="$ORDER" \
      -e PJB_VLLM_URL="$VLLM" -e PJB_MIN_FREE_GB="$MIN_FREE_GB" -e PJB_FORCE="$FORCE" -e PJB_WIKI="$WIKI" \
      "bash '$0' _loop '$SCAN_ROOT'; echo; echo 'queue finished - Ctrl-b d to leave'; exec bash"
    echo "started tmux session $SESSION (attach: $0 attach)" ;;
  _loop) SCAN_ROOT=$2; loop ;;
  status)
    tmux has-session -t "=$SESSION" 2>/dev/null && echo "session $SESSION: running" || echo "session $SESSION: not running"
    [ -e "$HOME_DIR/current" ] && echo "current: $(cat "$HOME_DIR/current")"
    echo "OK: $(grep -c '^OK' "$DONE")   FAIL: $(grep -c '^FAIL' "$DONE")   SKIP: $(grep -c '^SKIP' "$DONE")"
    tail -n 5 "$DONE"; echo "--"; tail -n 5 "$HOME_DIR/queue.log" 2>/dev/null ;;
  attach) tmux attach -t "=$SESSION" ;;
  stop)
    if [ "${2:-}" = --now ]; then tmux kill-session -t "=$SESSION" && echo "killed (OCR resumes on next start)"
    else touch "$HOME_DIR/STOP"; echo "will stop after the current volume"; fi ;;
  *) sed -n 2,22p "$0"; exit 1 ;;
esac
