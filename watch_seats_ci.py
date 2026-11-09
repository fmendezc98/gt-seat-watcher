#!/usr/bin/env python3
"""
GitHub Actions variant of the OSCAR seat watcher.

Runs a bounded polling loop (RUN_MINUTES) then exits, so a cron schedule can
restart it. Overlapping runs are fine and intentional - they remove blind gaps
caused by GitHub's scheduler delays.

Config via env:
  NTFY_TOPIC   (required)  ntfy.sh topic to publish to
  RUN_MINUTES  (default 35) how long this invocation loops before exiting
"""

import datetime as dt
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TERM = "202702"  # Spring 2027
TERM_LABEL = "Spring 2027"

COURSES = [
    {"crn": "29737", "name": "CS 8803 O15 - Intro to Computer Law"},
    {"crn": "28330", "name": "MGT 6311 - Digital Marketing (CS students)"},
]

EASTERN = ZoneInfo("America/New_York")
# Stop at end of Mon Jan 18, 2027 Eastern (Georgia Tech local time).
DEADLINE = dt.datetime(2027, 1, 19, 0, 0, 0, tzinfo=EASTERN)

POLL_SECONDS = 60   # seats observed open for only ~2 min
JITTER_SECONDS = 10
REOPEN_REMINDER_SECONDS = 600

BANNER = ("https://registration.banner.gatech.edu/StudentRegistrationSsb"
          "/ssb/searchResults/getEnrollmentInfo")
REGISTER_URL = ("https://registration.banner.gatech.edu/StudentRegistrationSsb"
                "/ssb/registration")

TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
RUN_MINUTES = int(os.environ.get("RUN_MINUTES", "35"))

# healthchecks.io dead-man's switch. Optional: unset = pinging disabled.
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "").strip().rstrip("/")


def log(msg):
    print(f"[{dt.datetime.now(EASTERN):%Y-%m-%d %H:%M:%S %Z}] {msg}", flush=True)


SEATS_RE = re.compile(
    r"Enrollment Seats Available:\s*</span>\s*<span[^>]*>\s*(-?\d+)", re.I)
ACTUAL_RE = re.compile(
    r"Enrollment Actual:\s*</span>\s*<span[^>]*>\s*(-?\d+)", re.I)
MAX_RE = re.compile(
    r"Enrollment Maximum:\s*</span>\s*<span[^>]*>\s*(-?\d+)", re.I)


def fetch_seats(crn):
    body = urllib.parse.urlencode(
        {"term": TERM, "courseReferenceNumber": crn}).encode()
    req = urllib.request.Request(
        BANNER, data=body,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        })
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    m = SEATS_RE.search(html)
    if not m:
        raise ValueError(f"unparseable response for CRN {crn}: {html[:200]!r}")
    a = ACTUAL_RE.search(html)
    x = MAX_RE.search(html)
    return int(m.group(1)), int(a.group(1)) if a else -1, int(x.group(1)) if x else -1


def notify(title, message, priority="default", tags="bell", click=None):
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        "https://ntfy.sh/" + TOPIC, data=message.encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        log(f"  -> ntfy sent: {title}")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"  !! ntfy FAILED ({title}): {exc}")
        return False


def selftest():
    """One fetch per CRN + one push. Exits nonzero if anything is unreachable."""
    if not TOPIC:
        log("FATAL: NTFY_TOPIC not set")
        return 1
    lines, ok = [], True
    for c in COURSES:
        try:
            avail, actual, mx = fetch_seats(c["crn"])
            log(f"  OK  {c['name']} CRN {c['crn']}: {avail} open ({actual}/{mx})")
            lines.append(f"{c['name']}: {avail} open ({actual}/{mx})")
        except Exception as exc:  # noqa: BLE001
            ok = False
            log(f"  FAIL {c['name']} CRN {c['crn']}: {exc}")
            lines.append(f"{c['name']}: UNREACHABLE - {exc}")
    if not ok:
        log("SELFTEST FAILED: OSCAR unreachable from this runner.")
        notify("Cloud watcher SELFTEST FAILED",
               "GitHub runner could not reach OSCAR:\n" + "\n".join(lines),
               priority="high", tags="warning")
        return 1
    if not notify("Cloud watcher verified",
                  "Running on GitHub Actions - no laptop needed.\n\n"
                  + "\n".join(lines)
                  + f"\n\nTerm {TERM} ({TERM_LABEL}). Polling every ~1 min until "
                  f"end of {(DEADLINE - dt.timedelta(minutes=1)):%a %b %d, %Y} ET.",
                  priority="default", tags="white_check_mark",
                  click=REGISTER_URL):
        return 1
    log("SELFTEST PASSED.")
    return 0


def hc_ping(suffix="", quiet=True):
    """Ping healthchecks.io. Never raises - monitoring must not break polling."""
    if not HEALTHCHECK_URL:
        return
    try:
        req = urllib.request.Request(HEALTHCHECK_URL + suffix,
                                     headers={"User-Agent": "gt-seat-watcher"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        if not quiet:
            log(f"  -> healthcheck ping{suffix or ' (ok)'}")
    except Exception as exc:  # noqa: BLE001
        log(f"  !! healthcheck ping{suffix} failed: {exc}")


def github_seatwatch_healthy(max_age_min=360):
    """True if seat-watch is running now, or ran recently. Returns (ok, detail)."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        return None, "no GITHUB_TOKEN/GITHUB_REPOSITORY - skipped"
    url = (f"https://api.github.com/repos/{repo}/actions/workflows/"
           f"seat-watch.yml/runs?per_page=10")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "seat-watchdog",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    now = dt.datetime.now(dt.timezone.utc)
    newest = None
    for run in data.get("workflow_runs", []):
        if run["status"] in ("in_progress", "queued"):
            return True, f"seat-watch {run['status']} (run {run['id']})"
        created = dt.datetime.fromisoformat(
            run["created_at"].replace("Z", "+00:00"))
        if newest is None or created > newest[0]:
            newest = (created, run)

    if newest is None:
        return False, "no seat-watch runs found at all"
    age_min = (now - newest[0]).total_seconds() / 60
    run = newest[1]
    detail = (f"last seat-watch run {age_min:.0f} min ago "
              f"({run['status']}/{run['conclusion']})")
    if age_min > max_age_min:
        return False, detail
    # Recent but broken is still broken - a crash-looping job polls nothing.
    if run["conclusion"] in ("failure", "cancelled", "timed_out",
                             "startup_failure"):
        return False, detail + " - last run did not succeed"
    return True, detail


def watchdog():
    """Hourly check. SILENT when healthy - pushes only on problems or open seats."""
    if not TOPIC:
        log("FATAL: NTFY_TOPIC not set")
        return 1

    now = dt.datetime.now(EASTERN)
    if now >= DEADLINE:
        log(f"Past deadline ({DEADLINE:%Y-%m-%d %H:%M %Z}). Nothing to do.")
        return 0

    problems, lines, open_now = [], [], []
    for c in COURSES:
        try:
            avail, actual, mx = fetch_seats(c["crn"])
            log(f"  OK  {c['name']} CRN {c['crn']}: {avail} open ({actual}/{mx})")
            lines.append(f"{c['name']}: {avail} open ({actual}/{mx})")
            if avail > 0:
                open_now.append((c, avail, actual, mx))
        except Exception as exc:  # noqa: BLE001
            log(f"  FAIL {c['name']} CRN {c['crn']}: {exc}")
            lines.append(f"{c['name']}: UNREACHABLE")
            problems.append(f"OSCAR unreachable for CRN {c['crn']}")

    # An open seat always gets through, watchdog or not.
    for c, avail, actual, mx in open_now:
        notify(
            f"SEAT OPEN: {c['name']}",
            f"{c['name']}\n"
            f"CRN {c['crn']} - {TERM_LABEL} (term {TERM})\n"
            f"{avail} seat(s) available ({actual}/{mx} filled).\n\n"
            f"REGISTER IMMEDIATELY - this can close again in minutes.\n"
            f"{REGISTER_URL}",
            priority="urgent", tags="rotating_light,books", click=REGISTER_URL)

    try:
        healthy, detail = github_seatwatch_healthy()
        log(f"  seat-watch health: {healthy} - {detail}")
        if healthy is False:
            problems.append(f"seat-watch not running: {detail}")
    except Exception as exc:  # noqa: BLE001
        log(f"  ?? could not check seat-watch health: {exc}")

    if problems:
        notify("Seat watcher is DOWN",
               "Problems detected:\n- " + "\n- ".join(problems)
               + "\n\nLatest readings:\n" + "\n".join(lines)
               + "\n\nRestart:\n"
               "gh workflow run seat-watch.yml --repo " 
               + os.environ.get("GITHUB_REPOSITORY", "<repo>"),
               priority="high", tags="warning")
        log("WATCHDOG: PROBLEMS REPORTED.")
        return 1

    log("WATCHDOG OK (silent - no notification sent).")
    return 0


def main():
    if not TOPIC:
        log("FATAL: NTFY_TOPIC is not set. Add it as a repository secret.")
        return 1

    now = dt.datetime.now(EASTERN)
    if now >= DEADLINE:
        log(f"Past deadline ({DEADLINE:%Y-%m-%d %H:%M %Z}). Nothing to do.")
        return 0

    stop_at = min(now + dt.timedelta(minutes=RUN_MINUTES), DEADLINE)
    log(f"Run starting. Term {TERM}. Looping until {stop_at:%H:%M:%S %Z} "
        f"(hard deadline {DEADLINE:%Y-%m-%d %H:%M %Z}).")

    state = {c["crn"]: {"open": False, "last_alert": 0.0} for c in COURSES}
    failures = 0
    bad_sweeps = 0
    hc_ping("/start", quiet=False)

    while dt.datetime.now(EASTERN) < stop_at:
        sweep_ok = True
        for course in COURSES:
            crn, name = course["crn"], course["name"]
            try:
                avail, actual, mx = fetch_seats(crn)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                sweep_ok = False
                log(f"  ?? {name} CRN {crn}: {exc}")
                continue
            failures = 0
            st = state[crn]
            log(f"  {name} CRN {crn}: {avail} open ({actual}/{mx})")

            if avail > 0:
                t = time.time()
                first = not st["open"]
                if first or t - st["last_alert"] >= REOPEN_REMINDER_SECONDS:
                    notify(
                        f"{'SEAT OPEN' if first else 'STILL OPEN'}: {name}",
                        f"{name}\n"
                        f"CRN {crn} - {TERM_LABEL} (term {TERM})\n"
                        f"{avail} seat(s) available ({actual}/{mx} filled).\n\n"
                        f"REGISTER IMMEDIATELY - this can close again in minutes.\n"
                        f"{REGISTER_URL}",
                        priority="urgent", tags="rotating_light,books",
                        click=REGISTER_URL)
                    st["last_alert"] = t
                st["open"] = True
            else:
                if st["open"]:
                    notify(f"Closed again: {name}",
                           f"{name} (CRN {crn}) is back to 0 seats. Still watching.",
                           priority="low", tags="lock")
                st["open"] = False
                st["last_alert"] = 0.0

        if failures >= 9:  # 3 full sweeps all failing
            notify("Seat watcher: OSCAR unreachable",
                   "Three straight sweeps failed from GitHub Actions. "
                   "OSCAR may be down or blocking the runner.",
                   priority="high", tags="warning")
            failures = 0

        # Heartbeat to the external dead-man's switch. One transient fetch
        # error is not an outage - only escalate to /fail after 3 consecutive
        # bad sweeps, otherwise stay silent and let the grace period judge.
        if sweep_ok:
            bad_sweeps = 0
            hc_ping()
        else:
            bad_sweeps += 1
            log(f"  (bad sweep {bad_sweeps}/3 - withholding healthcheck ping)")
            if bad_sweeps >= 3:
                hc_ping("/fail", quiet=False)

        nap = POLL_SECONDS + random.randint(-JITTER_SECONDS, JITTER_SECONDS)
        left = (stop_at - dt.datetime.now(EASTERN)).total_seconds()
        if left <= 0:
            break
        time.sleep(min(nap, left))

    log("Run finished (next scheduled run picks up).")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    if len(sys.argv) > 1 and sys.argv[1] == "--watchdog":
        sys.exit(watchdog())
    sys.exit(main())
