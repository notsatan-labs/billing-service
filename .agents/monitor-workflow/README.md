# monitor-workflow

Wait until **all** GitHub Actions runs for a pushed commit SHA finish. The
script never touches git — push first, then hand it the SHA. Results are
reported on **stdout** (no file writes).

Polling (~15s base + up to 5s jitter) is sized so **three parallel** monitors
(different commits) stay well under GitHub API rate limits. Three is a soft
target, not a cap; more instances work with less headroom.

## Requirements

- `gh` authenticated (`gh auth status`); the repo is inferred from this checkout

## Usage

```bash
SHA=$(git rev-parse HEAD)
git push origin "$SHA:refs/heads/master"

# Monitor whatever workflows GitHub creates for that SHA (full 40-char SHA)
python3 .agents/monitor-workflow/monitor.py "$SHA"
```

The SHA is the only argument; everything else is fixed (see below).

## Agent usage

1. Commit locally, record the SHA and push that commit (as above).
2. Start the monitor in the **background**; several can run at once.
3. When finished, stdout ends with:

   ```text
   MONITOR_JSON: {"ok":true,"summary":"PASS","sha":"...","runs":[...],...}
   MONITOR_RESULT: PASS
   ```

   or `MONITOR_RESULT: FAIL` (JSON still printed first; failed step logs are
   printed above it).

4. Prefer matching `MONITOR_RESULT:` via shell notify / reading the terminal.

## How “all workflows” works

GitHub does not say “N workflows will start” up front. The script:

1. Polls `gh run list --commit <sha>` until ≥1 run appears (gives up after 3 minutes)
2. Waits until the set of run IDs is **unchanged for 20 seconds**
   so late-starting workflows are included — works when you add more workflow files later
3. Polls until every run for that SHA is `completed` (gives up after 30 minutes; still picks up stragglers)
4. PASS only if every `conclusion` is `success` (`skipped`, `neutral` and `cancelled` all count as FAIL)

## Rate limits

Each instance makes a `gh run list` call every 15s plus 0–5s random jitter so
instances don't sync up, i.e. ≤ 240 calls/hour per instance. Sized for three
concurrent instances (≈ 720 calls/hour), well under the authenticated REST
budget (~5000/h).
Failed-log fetches only run on failure.
