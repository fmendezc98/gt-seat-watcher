# GT OSCAR Seat Watcher (GitHub Actions)

Cloud watcher for Fall 2026 (term `202608`) seat openings. Runs without a laptop.

| Course | CRN | Section |
|---|---|---|
| MGT 6311 Digital Marketing | 87967 | O01 |
| CS 6603 AI Ethics and Society | 87912 | O01 |
| CS 8803 Intro to Computer Law | 88875 | O15 |

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

## Stopping early

```sh
gh workflow disable seat-watch.yml
```
