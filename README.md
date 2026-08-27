# GT OSCAR Seat Watcher (GitHub Actions)

Cloud watcher for Fall 2026 (term `202608`) seat openings. Runs without a laptop.

| Course | CRN | Section |
|---|---|---|
| MGT 6311 Digital Marketing | 87967 | O01 |
| CS 6603 AI Ethics and Society | 87912 | O01 |
| CS 8803 Intro to Computer Law | 88875 | O15 |
| CS 6300 Software Dev Process | 83929 | O01 |

Polls every ~2 min and pushes to ntfy.sh when a section goes from 0 seats to >0.
Auto-stops after **Fri Aug 28 2026, 11:59pm Eastern** (the script no-ops past
that, so leftover scheduled runs cost nothing).

## Setup

The ntfy topic is read from the `NTFY_TOPIC` repository secret - it is
deliberately not in this code, because ntfy topics are unauthenticated and
anyone who knows the name can read the alerts.

```sh
gh secret set NTFY_TOPIC --body '<your-topic>'
gh workflow run seat-watch.yml     # start immediately
gh run watch                       # follow it
```

## Alerts

You are notified **only when something needs you**:

- `SEAT OPEN: <course>` - urgent (prio 5), repeats every 10 min while open
- `Seat watcher is DOWN` - hourly watchdog found a problem
- `Closed again: <course>` - low priority, seat was taken

No "still alive" pings. The `watchdog` workflow runs hourly and stays
**silent** unless seat-watch has stopped, is crash-looping, or OSCAR is
unreachable.

## Stopping early

```sh
gh workflow disable seat-watch.yml
```
