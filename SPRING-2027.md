# Reusing this for Spring 2027

Nothing here runs on its own. `relay.yml` and `verify.yml` are
`workflow_dispatch` only - no cron anywhere in the repo. To restart the
watch when registration opens (~late Oct 2026):

## 1. Confirm the term code

Do not guess it. Fall 2026 was `202608`; Spring 2027 is *probably* `202702`,
but check:

    curl -s -c /tmp/j -b /tmp/j -o /dev/null \
      "https://registration.banner.gatech.edu/StudentRegistrationSsb/ssb/term/termSelection?mode=search"
    curl -s -c /tmp/j -b /tmp/j \
      "https://registration.banner.gatech.edu/StudentRegistrationSsb/ssb/classSearch/getTerms?searchTerm=&offset=1&max=10"

## 2. Confirm each CRN maps to the course you think it does

CRNs change every term. Verify before trusting them - last time a CRN
labelled "6600" turned out to be CS 6300:

    curl -s -X POST \
      "https://registration.banner.gatech.edu/StudentRegistrationSsb/ssb/searchResults/getEnrollmentInfo" \
      -d "term=<TERM>&courseReferenceNumber=<CRN>"

## 3. Edit `watch_seats_ci.py`

- `TERM`      - new term code
- `COURSES`   - new CRNs and names
- `DEADLINE`  - new stop date (Eastern)

## 4. Secrets

`NTFY_TOPIC` is still set. `HEALTHCHECK_URL` points at a check that was
deleted after Fall 2026 - make a new healthchecks.io check and update it:

    gh secret set HEALTHCHECK_URL --body '<new ping url>'

## 5. Start it

    gh workflow run verify.yml    # confirm OSCAR + ntfy reachable first
    gh workflow run relay.yml     # 7 legs x 5.5h = 38.5h of coverage

## Hard-won notes

- **GitHub `schedule` is unreliable.** On this repo the first cron fired
  ~7.5h late. That is why coverage uses a chained relay instead of cron.
- **Use `if: ${{ !cancelled() }}`, never `always()`**, on the relay legs.
  `always()` is true on cancellation, making the chain impossible to stop.
- **Poll every 60s.** Seats observed open for only 1-3 minutes; a 2-minute
  interval misses them.
- **Debounce the healthcheck `/fail`** (3 consecutive bad sweeps). A single
  transient fetch error otherwise raises a false DOWN alert.
- Fall 2026 result: 1,454 sweeps, 21 seat-open alerts, 0 false alarms.
