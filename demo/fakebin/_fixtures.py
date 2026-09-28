#!/usr/bin/env python3
"""Fabricated Slurm data for the toy 'Baker Street Lab' on 'Cluster A' demo (characters from
The Adventures of Sherlock Holmes, public domain). Shared by the fake sacctmgr/sshare/sacct/
scontrol scripts in this folder. Nothing here is real: it's invented data for a fictional lab,
generated fresh (relative to "now") every time the demo runs, so it always looks current and
never drifts stale.
"""
import datetime as dt, math

NOW = dt.datetime.now()
HALF_LIFE_DAYS = 14.0                # this toy cluster decays usage faster than a real 30-day one
H = HALF_LIFE_DAYS * 24.0            # hours
K = H / math.log(2)
CAP_BILL_H = 6000.0
CAP_GPU_H = 80.0
RESET_DAYS_AGO = 20.0   # the boundary between "since the toy reset" jobs and older, forgotten ones
GHOST_USERS = ["moran"]   # zero usage AND no job in the last 90 days: should be dropped entirely

PARTITIONS = {   # name -> (cpu weight, mem weight per GB, gpu weight) | None = no TRESBillingWeights at all
    "yard": None,                        # deliberately unconfigured: shows up in "what ran", not in "what a job costs"
    "hound": (2.0, 0.10, 60.0),          # modest GPU partition
    "reichenbach": (3.0, 0.15, 500.0),   # the expensive one
}

# (user, partition, billing rate per hour, gpus, days_ago the job started, hours it ran)
# "days_ago" < ~20 are the jobs since the toy reset; the later ones (25+ days ago) are older jobs
# that Slurm's own usage total no longer counts, which is exactly what lets the page infer a reset.
JOBS = [
    ("watson", "yard", 0, 0, 1.2, 3.0), ("watson", "hound", 60, 1, 3.5, 4.0), ("watson", "yard", 0, 0, 7.0, 2.0),
    ("watson", "yard", 0, 0, 0.5, 1.0),
    ("holmes", "hound", 60, 1, 0.8, 6.0), ("holmes", "reichenbach", 500, 1, 5.0, 1.5), ("holmes", "yard", 0, 0, 10.0, 2.0),
    ("holmes", "hound", 60, 1, 14.0, 2.0),
    ("lestrade", "yard", 0, 0, 2.0, 5.0), ("lestrade", "hound", 120, 2, 9.0, 3.0), ("lestrade", "yard", 0, 0, 11.0, 3.0),
    ("mycroft", "reichenbach", 500, 1, 4.0, 2.0), ("mycroft", "yard", 0, 0, 15.0, 4.0), ("mycroft", "hound", 60, 1, 1.0, 5.0),
    ("adler", "yard", 0, 0, 6.0, 8.0), ("adler", "yard", 0, 0, 12.0, 1.0), ("adler", "reichenbach", 500, 1, 16.0, 1.0),
    ("moriarty", "yard", 0, 0, 18.0, 0.5), ("moriarty", "yard", 0, 0, 2.0, 1.0), ("moriarty", "hound", 60, 1, 8.0, 2.0),
    ("hudson", "yard", 0, 0, 2.0, 1.0),   # recently active (shows up in ACTIVE_RECENT) but zero billing either way
    # older jobs, from before the toy reset: real Slurm's own usage total has already forgotten these
    ("watson", "reichenbach", 500, 1, 25.0, 10.0), ("lestrade", "reichenbach", 500, 1, 32.0, 6.0),
    ("holmes", "hound", 60, 1, 40.0, 20.0), ("mycroft", "reichenbach", 500, 1, 55.0, 8.0),
]
USERS = sorted({j[0] for j in JOBS})


def job_times(days_ago, hours):
    end = NOW - dt.timedelta(days=days_ago)
    start = end - dt.timedelta(hours=hours)
    return start, end


def fmt_dt(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def fmt_elapsed(hours):
    total_seconds = int(round(hours * 3600))
    d, rem = divmod(total_seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return f"{d}-{h:02d}:{m:02d}:{s:02d}" if d else f"{h:02d}:{m:02d}:{s:02d}"


def decayed_contribution(rate, days_ago, hours):
    """Same integral build_usage_page.py's detect_reset() uses: a job billing at a constant hourly
    rate, faded continuously from when it ran until now."""
    start, end = job_times(days_ago, hours)
    A = (NOW - start).total_seconds() / 3600
    B = (NOW - end).total_seconds() / 3600
    return rate * K * (2 ** (-B / H) - 2 ** (-A / H))


def jobs_since(cutoff_dt):
    for j in JOBS:
        start, end = job_times(j[4], j[5])
        if start >= cutoff_dt:
            yield j, start, end


def account_totals():
    """The account's current decayed billing total (what a real sshare would report). Only jobs
    since the toy reset count: Slurm's own usage total has already forgotten the older ones,
    even though sacct still lists them. That gap is exactly what lets the page infer the reset."""
    bill = sum(decayed_contribution(j[2], j[4], j[5]) for j in JOBS if j[4] < RESET_DAYS_AGO)
    gpu = sum(j[3] * j[5] for j in JOBS if j[4] < RESET_DAYS_AGO) * 0.8   # a rough decayed-ish
    return bill, gpu                                                      # figure; GPU isn't reset-fitted


def user_totals():
    """Per-user shares, scaled so they add up to the account total (a simplification: Slurm
    actually decays each user's usage independently), fine for a toy example."""
    bill_total, gpu_total = account_totals()
    raw_bill = {u: 0.0 for u in USERS}
    raw_gpu = {u: 0.0 for u in USERS}
    for u, part, rate, gpus, days_ago, hours in JOBS:
        if days_ago >= RESET_DAYS_AGO:
            continue
        raw_bill[u] += rate * hours
        raw_gpu[u] += gpus * hours
    rb_sum = sum(raw_bill.values()) or 1.0
    rg_sum = sum(raw_gpu.values()) or 1.0
    return ({u: raw_bill[u] / rb_sum * bill_total for u in USERS},
            {u: raw_gpu[u] / rg_sum * gpu_total for u in USERS})
