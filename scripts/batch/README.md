# Batch run (many volumes, unattended)

Runs runbook steps 4–8 for every volume in a scan folder, oldest year first,
inside the tmux session `pjb_batch`, so the run does not depend on an open SSH
connection.

```
SCANS/                      one subfolder per volume, page images inside
  PJB_1959/0001.jpg …       the year is taken from the folder name
  2026-11-24_10-00_XIX_1977_finished/jpg/0001.jpg …
```

```bash
# [chgpu]
cd ~/Passauer_Jahrbuecher_Pipeline
scripts/batch/pjb_batch.sh plan  ~/SCANS     # check years, volume numbers, image counts
scripts/batch/pjb_batch.sh start ~/SCANS     # start the queue
scripts/batch/pjb_batch.sh status            # progress
scripts/batch/pjb_batch.sh attach            # watch (leave with Ctrl-b d)
scripts/batch/pjb_batch.sh stop              # stop after the current volume (--now: at once)
```

For each volume the queue

1. writes `configs/pjb-NNN-YYYY-gpubox.yaml` (volume number = year − 1958;
   exceptions and other titles go into `volumes.tsv`, see `plan.py`),
2. waits until the Chandra vLLM server answers (`PJB_VLLM_URL`, default
   `http://localhost:8000/v1`), then runs `pjb-pipeline run`. If the server
   disappears for 5 minutes, the run is stopped and resumed later from the OCR
   cache. A volume is only accepted when every page has OCR output; after 3
   failed attempts it is marked FAIL and the queue moves on,
3. runs `check_reading_order.py` (report in `~/pjb_batch/logs/<tag>.check.txt`;
   more than 15 broken joins or stranded continuations are noted in the commit
   message and in `done.txt`),
4. adds the volume to the wiki and commits it, commits the config, and pushes
   both repos to the branch `PJB_BRANCH` (default `batch-run-2026-11`, created
   from `main`). Each repo gets one pull request for the whole run.

State lives in `~/pjb_batch/`: `done.txt` (one OK/FAIL/SKIP line per volume),
`queue.log`, `logs/<tag>.log`. On a restart, OK volumes and volumes already in
the wiki are skipped. FAIL volumes are tried again.
