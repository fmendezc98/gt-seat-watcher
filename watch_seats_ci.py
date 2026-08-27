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
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TERM = "202608"  # Fall 2026

COURSES = [
    {"crn": "87967", "name": "MGT 6311 - Digital Marketing (O01)"},
    {"crn": "87912", "name": "CS 6603 - AI Ethics and Society (O01)"},
    {"crn": "88875", "name": "CS 8803 - Intro to Computer Law (O15)"},
]

EASTERN = ZoneInfo("America/New_York")
# Stop at end of Fri Aug 28, 2026 Eastern (Georgia Tech local time).
DEADLINE = dt.datetime(2026, 8, 29, 0, 0, 0, tzinfo=EASTERN)

POLL_SECONDS = 120
JITTER_SECONDS = 15
REOPEN_REMINDER_SECONDS = 600

BANNER = ("https://registration.banner.gatech.edu/StudentRegistrationSsb"
          "/ssb/searchResults/getEnrollmentInfo")
REGISTER_URL = ("https://registration.banner.gatech.edu/StudentRegistrationSsb"
                "/ssb/registration")

TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
RUN_MINUTES = int(os.environ.get("RUN_MINUTES", "35"))


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
                  + "\n\nPolling every ~2 min until Fri Aug 28, 11:59pm ET.",
                  priority="default", tags="white_check_mark",
                  click=REGISTER_URL):
        return 1
    log("SELFTEST PASSED.")
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

    while dt.datetime.now(EASTERN) < stop_at:
        for course in COURSES:
            crn, name = course["crn"], course["name"]
            try:
                avail, actual, mx = fetch_seats(crn)
            except Exception as exc:  # noqa: BLE001
                failures += 1
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
                        f"CRN {crn} - Fall 2026 (term 202608)\n"
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
    sys.exit(main())
