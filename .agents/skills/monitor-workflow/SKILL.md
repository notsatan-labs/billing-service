---
name: monitor-workflow
description: >-
  Push a commit upstream and monitor all GitHub Actions runs for its SHA via gh
  until pass/fail. Use after creating a commit when you need CI results, or
  when asked to push-and-watch, monitor workflows, or wait on Actions.
---

# Monitor Workflow

```bash
python3 .agents/monitor-workflow/monitor.py <full-sha>
```

The SHA is the only argument. The script only watches; pushing is a separate step you do first.

## When to use

- After a local commit that should be verified on GitHub Actions
- Push-and-watch / wait on Actions
- Background the script so other work can continue (several monitors can run at once)

## Procedure

1. Commit, then record the SHA and push exactly that commit:

   ```bash
   SHA=$(git rev-parse HEAD)
   git push origin "$SHA:refs/heads/master"
   ```

2. Start the monitor in the **background**:

   ```bash
   python3 .agents/monitor-workflow/monitor.py "$SHA"
   ```

   Prefer `notify_on_output` / terminal capture on `MONITOR_RESULT:`.

3. Treat these stdout markers as the result (no project files are written):

   - `MONITOR_JSON: {...}` — full structured payload (one line)
   - `MONITOR_RESULT: PASS` or `MONITOR_RESULT: FAIL`

4. On `FAIL`, use the JSON `failed` list / printed logs, fix, recommit, monitor again.

## Behavior notes

- Push each commit on its own, then start its monitor. Each drops out of the in-flight set when its runs finish.
- The SHA must be the full 40 characters; `gh` doesn't match short ones.
- Monitors **all** Actions runs for the SHA (settle-window discovery — future workflows are included automatically).
- Polling (every 15s + up to 5s jitter) is fixed and sized for three concurrent monitors; that's a soft target, not a cap.
- CI only triggers on pushes to `main`/`master` and on pull requests. A commit on a feature branch without an open PR gets no runs and the monitor times out.
- The result markers are printed on every exit path, including bad arguments, Ctrl-C and SIGTERM.
- Details: `.agents/monitor-workflow/README.md`
