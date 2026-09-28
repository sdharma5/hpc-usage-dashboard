#!/usr/bin/env python3
"""HPC usage page for one Slurm account. No scraping: everything comes from the scheduler itself.
  sacctmgr  -> the caps (GrpTRESMins: whichever TRES types this account has a limit on, not
               assumed to be billing+GPU; could be just billing, just GPU, cpu-hours, memory,
               nodes, a license, or several of these at once. Discovered fresh each run.)
  sshare    -> what currently counts against those caps (decayed usage, per user)
  sacct     -> what jobs were charged recently (per user, per partition)
  scontrol  -> the per-partition billing weights (for "what a job costs now", only shown when
               the account actually has a billing cap and the cluster has weights configured),
               and the configured priority decay half-life (for the reset-detection math)
Settings come from config.env next to this file's parent directory (see config.example.env);
run setup.sh once to create it. Any setting can be overridden with an environment variable of
the same name, e.g. ACCOUNT=otheracct python3 build_usage_page.py.
Writes index.html, index_artifact.html and bars_only.html next to config.env.
The weighted "billing" cost model (a job's hourly rate is the LARGEST of cores*w_cpu, GB*w_mem,
gpus*w_gpu) is how Slurm's own TRESBillingWeights are normally configured, and is only used for
the "what a job costs now" table when this account actually has a billing cap. Any other capped
resource (cpu-hours, memory, nodes, a license, ...) is charged 1:1, with no weighting, since raw
TRES caps aren't weighted the way billing is; the page shows the plain Slurm accounting fields
behind it instead of a cost formula that wouldn't apply."""
import subprocess, os, re, html, json, math, colorsys, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HPCUSAGE_ROOT") or os.path.dirname(HERE)   # config.env, index.html etc. live here (normally the repo root, next to setup.sh; HPCUSAGE_ROOT overrides this, e.g. for demo/)

def load_config():
    cfg = {}
    path = os.path.join(ROOT, "config.env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1); cfg[k.strip()] = v.strip().strip("'\"")
    for k in list(cfg):    # an environment variable of the same name always wins
        if k in os.environ: cfg[k] = os.environ[k]
    return cfg
CFG = load_config()
def cfg(key, default=""): return os.environ.get(key, CFG.get(key, default))

if not os.path.exists(os.path.join(ROOT, "config.env")):
    raise SystemExit(f"no config.env in {ROOT}: run ./setup.sh once first (see config.example.env)")

LAB_NAME, CLUSTER_NAME, APP_SLUG = cfg("LAB_NAME", "My Lab"), cfg("CLUSTER_NAME", "MyCluster"), cfg("APP_SLUG", "hpc-usage")
ACCOUNT = cfg("ACCOUNT")
REFRESH_HOUR, TIMEZONE_LABEL = cfg("REFRESH_HOUR", "06:00"), cfg("TIMEZONE_LABEL", "")
REF_CORES, REF_GB = int(cfg("REF_CORES", "8")), int(cfg("REF_GB", "64"))
ACCENT = cfg("ACCENT", "#2563eb")
PINNED_USER = cfg("PINNED_USER", "").strip()
if not ACCOUNT: raise SystemExit("config.env: ACCOUNT is not set")
OUT = os.path.join(ROOT, "index.html")

def sh(cmd):
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True).stdout

def tres(s):
    d = {}
    for kv in s.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1); d[k] = v
    return d

def step(msg): print(msg, flush=True)   # progress messages -- some of these Slurm queries take a while on a busy cluster

def hours(s):                       # Slurm "D-HH:MM:SS" | "HH:MM:SS" | "MM:SS"
    s = s.strip()
    if not s: return 0.0
    days = 0
    if "-" in s: d, s = s.split("-", 1); days = int(d)
    p = [float(x) for x in s.split(":")]
    while len(p) < 3: p.insert(0, 0.0)
    return days * 24 + p[0] + p[1] / 60 + p[2] / 3600

# ---- how long usage takes to fade, straight from Slurm's own config -------------------
def decay_half_life_hours():
    try:
        for line in sh("scontrol show config").splitlines():
            if line.strip().lower().startswith("prioritydecayhalflife"):
                v = line.split("=", 1)[1].strip()
                return 0.0 if v in ("0", "0-00:00:00", "(null)", "") else hours(v)
    except Exception: pass
    return 720.0   # 30 days, Slurm's usual default
H = decay_half_life_hours()          # hours; 0 means this cluster does not decay usage at all
DECAYS = H > 0

# ---- what this account is actually capped on -------------------------------------------
def tres_info(key):
    """(display title, short unit name) for a TRES key Slurm might report in GrpTRESMins. Covers
    the common cases by name; anything unrecognised still gets a plain, honest label from its own
    key rather than being mistaken for billing or GPU-hours."""
    if key == "billing": return "Billing-hours", "billing-hours"
    if key == "cpu": return "CPU-hours", "CPU-hours"
    if key == "mem": return "Memory-hours (GB)", "GB-hours"
    if key == "node": return "Node-hours", "node-hours"
    if key == "energy": return "Energy-hours", "energy-hours"
    if key.startswith("gres/gpu"):
        variant = key.split(":", 1)[1] if ":" in key else None
        return (f"{variant.upper()} GPU-hours" if variant else "GPU-hours"), "GPU-hours"
    if key.startswith("gres/"):
        sub = key.split("/", 1)[1]
        return f"{sub.upper()}-hours", f"{sub}-hours"
    if key.startswith("license/"):
        return f"{key.split('/', 1)[1]} license-hours", "license-hours"
    return f"{key}-hours", f"{key}-hours"
def tres_scale(key): return 1024.0 if key == "mem" else 1.0   # Slurm reports mem in MB; show GB

step("Checking your account's caps...")
_cap_lines = sh(f"sacctmgr -n -P show assoc where account={ACCOUNT} user= format=GrpTRESMins%200").strip().splitlines()
if not _cap_lines:
    raise SystemExit(f"sacctmgr found no association at all for account '{ACCOUNT}' (the query returned "
                      "nothing). This usually means the account name is wrong, or it's not a real Slurm "
                      "account (e.g. a storage/scratch allocation name rather than a compute one). Run "
                      f"`sacctmgr show account {ACCOUNT}` and `sacctmgr -n show assoc where account={ACCOUNT}` "
                      "yourself to check.")
cap = tres(_cap_lines[0])
if not cap:
    raise SystemExit(f"account {ACCOUNT} has no GrpTRESMins caps set at all: this tool needs at "
                      "least one cap to report on; see README's 'What info does this need to work?'")
CAP_KEYS = sorted(cap, key=lambda k: (k != "billing", not k.startswith("gres/gpu"), k))   # billing first, then GPU, then the rest alphabetically
CAPS = {k: float(cap[k]) / 60 / tres_scale(k) for k in CAP_KEYS}
PRIMARY = CAP_KEYS[0]             # the account's main capped resource (billing, if it has one)
HAS_BILLING = "billing" in CAP_KEYS

# ---- current (decayed) usage, per person ------------------------------------------------
step("Checking current usage...")
used = {}
for line in sh(f"sshare -A {ACCOUNT} -a -P -n -o User,GrpTRESRaw%400").splitlines():
    u, raw = line.split("|", 1); t = tres(raw)
    used[u.strip() or "(account)"] = {k: float(t.get(k, 0)) / 60 / tres_scale(k) for k in CAP_KEYS}
TOT = used.pop("(account)")
LEFT = {k: CAPS[k] - TOT.get(k, 0.0) for k in CAP_KEYS}
users = sorted(used.items(), key=lambda r: tuple(-r[1][k] for k in CAP_KEYS) + (r[0],))   # biggest first

# ---- recent charges from sacct --------------------------------------------------------
def charges(since):
    start = since.strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    for line in sh(f"sacct -A {ACCOUNT} -a -X -S {start} -P -n --format=User,Partition,AllocTRES%200,Elapsed").splitlines():
        u, part, tr, el = line.split("|")
        if not tr or "," in part: continue          # never-started jobs, or multi-partition pending requests
        t = tres(tr); h = hours(el)
        rows.append((u, part, {k: float(t.get(k, 0)) / tres_scale(k) * h for k in CAP_KEYS}, h))
    return rows

# ---- partition weights (only meaningful if this account has a billing cap) -------------
step("Checking partition costs...")
weights = {}
def _mem_weight_per_gb(s):
    """TRESBillingWeights' Mem entry can be written per MB (no suffix -- Slurm's native unit for
    memory), or per KB/GB/TB/PB (a K/G/T/P suffix), at whoever configured that partition's
    choice. Normalize whichever one it is to a weight per GB, since that's the unit the rest of
    this script (and REF_GB) uses."""
    m = re.match(r"^([\d.]+)\s*([KMGTP]?)B?$", (s or "0").strip(), re.I)
    if not m: return 0.0
    val, suf = float(m.group(1)), m.group(2).upper()
    per_gb = {"": 1024.0, "K": 1024.0 * 1024, "M": 1024.0, "G": 1.0, "T": 1.0 / 1024, "P": 1.0 / 1024 ** 2}
    return val * per_gb.get(suf, 1024.0)
for line in sh("scontrol show partition -o").splitlines():
    f = dict(x.split("=", 1) for x in line.split() if "=" in x)
    w = f.get("TRESBillingWeights")
    if not w: continue
    d = tres(w)
    gpu_w = next((float(v) for kk, v in d.items() if kk.upper().startswith("GRES/GPU")), 0.0)   # matches GRES/gpu or a typed GRES/gpu:a100
    weights[f["PartitionName"]] = (float(d.get("CPU", 0)), _mem_weight_per_gb(d.get("Mem")), gpu_w)
HAS_COST_MODEL = HAS_BILLING and bool(weights)   # is there a real per-partition weighted cost model to show?

def detect_reset(window_days=100, step_h=3):
    """This account's usage total may reset on some schedule (Slurm's PriorityUsageResetPeriod).
    Try every possible 'counting starts here' moment over the last window_days; for each, add up
    the account's job records from then on (fading with the cluster's own decay half-life, if any)
    and compare with Slurm's own current total for the PRIMARY capped resource. The moment that
    fits best is the reset, but only if it fits well AND counting the whole window would clearly
    over-count. Returns the date, or None when there is no evidence of a reset."""
    import numpy as np
    now_ = dt.datetime.now(); w0 = now_ - dt.timedelta(days=window_days); A, B, R = [], [], []
    for line in sh(f"sacct -A {ACCOUNT} -a -X -S {w0:%Y-%m-%dT%H:%M:%S} -P -n --format=Start,End,AllocTRES%200").splitlines():
        st, en, tr = line.split("|")
        if not tr or st in ("", "None", "Unknown"): continue
        try:
            a = dt.datetime.strptime(st, "%Y-%m-%dT%H:%M:%S"); b = now_ if en in ("", "Unknown", "None") else min(dt.datetime.strptime(en, "%Y-%m-%dT%H:%M:%S"), now_)
        except ValueError: continue
        if b > a: A.append((now_ - a).total_seconds() / 3600); B.append((now_ - b).total_seconds() / 3600); R.append(float(tres(tr).get(PRIMARY, 0)) / tres_scale(PRIMARY))
    tot_primary = TOT.get(PRIMARY, 0.0)
    if not A or tot_primary <= 0: return None
    A, B, R = np.array(A), np.array(B), np.array(R)
    def counted(t_age):          # usage counted only from t_age hours ago on
        A2 = np.minimum(A, t_age); m = B < A2
        if not DECAYS: return float((R[m] * (A2[m] - B[m])).sum())
        k = H / math.log(2); return float((R[m] * k * (2 ** (-B[m] / H) - 2 ** (-A2[m] / H))).sum())
    ages = np.arange(window_days * 24, 0, -step_h); errs = np.array([abs(counted(t) - tot_primary) / tot_primary for t in ages])
    best, none_reset = float(errs.min()), float(errs[0]); plateau = ages[errs <= best + 0.01]; T = now_ - dt.timedelta(hours=float(np.median(plateau)))
    print(f"reset detection (on {tres_info(PRIMARY)[0]}): best fit counting from {T:%Y-%m-%d %H:%M} (error {100 * best:.1f}%); counting everything would be off by {100 * none_reset:.0f}%")
    return T if (best <= 0.05 and none_reset - best >= 0.08 and T > w0 + dt.timedelta(days=2)) else None
step("Checking whether usage resets (this can take a while on a busy account)...")
try: _reset = detect_reset()
except Exception as ex: print("reset detection failed:", ex); _reset = None
RESET_SENTENCE = (f'A reset appears to have happened around <b class="rd">{_reset.strftime("%b %d").replace(" 0", " ")}</b> (usage from before then no longer counts), but this hasn\'t been confirmed by cluster staff.' if _reset else "")
# ---- what ran since the reset (or the past 30 days if no reset was detected) ---------------
_since = _reset if _reset else dt.datetime.now() - dt.timedelta(days=30)
RAN_TITLE = f'What we ran since the reset ({_reset.strftime("%b %d").replace(" 0", " ")})' if _reset else "What we ran in the past 30 days"
step("Checking recent job history...")
recent = charges(_since)
by_part = {}
for u, p, vals, h in recent:
    r = by_part.setdefault(p, {"jobs": 0, "hours": 0.0, **{k: 0.0 for k in CAP_KEYS}})
    r["jobs"] += 1; r["hours"] += h
    for k in CAP_KEYS: r[k] += vals[k]
parts = sorted(by_part.items(), key=lambda kv: -kv[1][PRIMARY])

# ---- html helpers ---------------------------------------------------------------------
e = html.escape
f0 = lambda x: f"{x:,.0f}"; f1 = lambda x: f"{x:,.1f}"; pc = lambda x: f"{100 * x:.1f}%"

COLORS_FILE = os.path.join(ROOT, "user_colors.json")
NSLOT = 24                     # more colour slots than people; nobody shares a colour
named = list(users)
try: old_slots = json.load(open(COLORS_FILE))
except Exception: old_slots = {}
PINNED = {PINNED_USER: 1} if PINNED_USER else {}     # this person always gets slot 1, the accent colour
slots = {u: sl for u, sl in PINNED.items() if u in [t[0] for t in named]}
for u, _ in named:          # keep a person's colour from day to day, unless someone else already has it
    if u in slots: continue    # pinned
    sl = old_slots.get(u)
    if isinstance(sl, int) and 1 <= sl <= NSLOT and sl not in slots.values(): slots[u] = sl
for u, _ in named:
    if u in slots: continue
    free = [i for i in range(1, NSLOT + 1) if i not in slots.values()]
    if free: slots[u] = free[0]                          # the common case: everyone gets their own colour
    else:                                                 # more named people than colour slots: reuse
        counts = {}                                       # whichever slot is currently shared by the
        for sl in slots.values(): counts[sl] = counts.get(sl, 0) + 1   # fewest people, so reuse spreads
        slots[u] = min(range(1, NSLOT + 1), key=lambda i: counts.get(i, 0))   # out as evenly as possible
json.dump(slots, open(COLORS_FILE, "w"), indent=1)

def prow(name, r):
    n = r["jobs"]; cells = "".join(f'<td class="n">{f0(r[k])}</td>' for k in CAP_KEYS)
    return f'<tr><td>{e(name).replace("_", "_<wbr>")}</td><td class="n">{n}</td>{cells}<td class="n">{f1(r["hours"] / n) if n else "–"}</td></tr>'
ZERO_ROW = {"jobs": 0, "hours": 0.0, **{k: 0.0 for k in CAP_KEYS}}
rows_parts = "".join(prow(p, r) for p, r in parts)                                              # every partition that ran jobs, biggest first
rows_parts += "".join(prow(p, ZERO_ROW) for p in sorted(set(weights) - {p for p, _ in parts}))  # then every other partition, with zeros
TRES_THS = "".join(f'<th class="n">{e(tres_info(k)[0])}</th>' for k in CAP_KEYS)

if HAS_COST_MODEL:
    order = sorted(weights, key=lambda p: weights[p][2] * 1e3 + weights[p][0])
    def cost_of(p):
        wc, wm, wg = weights[p]; gpu = wg > 0; rate = max(REF_CORES * wc, REF_GB * wm, wg if gpu else 0)
        can = LEFT["billing"] / rate if rate else 0
        if gpu and "gres/gpu" in CAP_KEYS: can = min(can, LEFT["gres/gpu"])   # only a real limit if this account also has a GPU-hours cap
        return rate, can
    def best_worst(names):
        """Among the given partitions, the ones we could keep running longest (best, green) and shortest (worst, red). Nothing is marked if they all tie."""
        v = {p: round(cost_of(p)[1]) for p in names}
        if len(set(v.values())) < 2: return set(), set()
        return {p for p in v if v[p] == max(v.values())}, {p for p in v if v[p] == min(v.values())}
    gpu_parts = [p for p in order if weights[p][2] > 0]
    BEST, WORST = best_worst(gpu_parts)
    _hrs = {p: cost_of(p)[1] for p in gpu_parts}
    _lv = sorted({round(v) for v in _hrs.values()})      # the distinct hour values, fewest first
    def grad(p):
        """Text colour: hue from red (fewest hours we could keep running) through orange and yellow to green (most). Each distinct value gets an equally spaced step, so neighbouring queues always look different."""
        t = _lv.index(round(_hrs[p])) / (len(_lv) - 1) if len(_lv) > 1 else 0.5
        return f"hsl({125 * t:.0f},100%,34%)"
    cost_rows = ""
    for p in order:
        wc, wm, wg = weights[p]; rate, can = cost_of(p)
        sty = f' style="color:{grad(p)};font-weight:bold"' if p in _hrs else ""
        mark = " (best GPU)" if p in BEST else (" (worst GPU)" if p in WORST else "")
        cost_rows += f'<tr><td>{e(p).replace("_", "_<wbr>")}</td><td class="n">{wc:g}</td><td class="n">{wm:g}</td><td class="n">{wg:g}</td><td class="n"{sty}>{f0(rate)}</td><td class="n"{sty}>{f0(can)}{mark}</td></tr>'
else:
    gpu_parts = []

now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
frac_primary = TOT.get(PRIMARY, 0.0) / CAPS[PRIMARY] if CAPS[PRIMARY] else 0
warn = f'<p class="warn">Only {pc(1 - frac_primary)} of our {tres_info(PRIMARY)[0].lower()} are left.</p>' if frac_primary >= 0.8 else ""
KATEX_CSS = open(os.path.join(HERE, "..", "math", "katex_inline.css")).read() if HAS_COST_MODEL else ""

# ---- colour palette, generated from ACCENT -----------------------------------------------
def _hex_to_hls(h): r, g, b = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]; return colorsys.rgb_to_hls(r, g, b)
def _hls_to_hex(h, l, s): r, g, b = colorsys.hls_to_rgb(h % 1.0, min(max(l, 0), 1), min(max(s, 0), 1)); return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
_ah, _al, _as = _hex_to_hls(ACCENT)
BASE_L = [ACCENT, _hls_to_hex(_ah + 40 / 360, 0.62, 0.72), _hls_to_hex(_ah - 25 / 360, 0.72, 0.55), _hls_to_hex(_ah + 70 / 360, 0.60, 0.68), _hls_to_hex(_ah - 12 / 360, 0.42, 0.55)]
def _lum(h):
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = [f(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5)]; return 0.2126 * r + 0.7152 * g + 0.0722 * b
def _text_on(h, ink="#161616"):   # white or ink, whichever reads better on this fill
    l = _lum(h); return "#ffffff" if 1.05 / (l + 0.05) >= (l + 0.05) / (_lum(ink) + 0.05) else ink
def _lab(hexc):
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = [f(int(hexc[i:i + 2], 16) / 255) for i in (1, 3, 5)]
    x, y, z = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, 0.2126 * r + 0.7152 * g + 0.0722 * b, (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    h = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return (116 * h(y) - 16, 500 * (h(x) - h(y)), 200 * (h(y) - h(z)))
def _de(a, b): return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5
def extend_palette(base, n, lo, hi):
    """More colours around the same accent hue (±45deg), spread out from each other in Lab space (perceptual distance) so no two people ever look alike."""
    cands = []
    hue_deg = _ah * 360
    for delta in range(-45, 46, 3):
        hue = ((hue_deg + delta) % 360) / 360
        for sat in (0.6, 0.78, 0.95):
            for k in range(7):
                r, g, b = colorsys.hls_to_rgb(hue, lo + k * (hi - lo) / 6, sat); cands.append("#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255)))
    pal = list(base); labs = [_lab(c) for c in pal]
    while len(pal) < n:
        best = max((c for c in cands if c not in pal), key=lambda c: min(_de(_lab(c), l) for l in labs)); pal.append(best); labs.append(_lab(best))
    return pal
PAL_L = extend_palette(BASE_L, NSLOT, 0.38, 0.78)
CLS = "".join(f".c{i}{{background:var(--c{i});color:var(--t{i})}}" for i in range(1, NSLOT + 1))
CLSF = "".join(f".f{i}{{fill:var(--c{i})}}.tf{i}{{fill:var(--t{i})}}" for i in range(1, NSLOT + 1))
SLOT_L = "".join(f"--c{i + 1}:{c};--t{i + 1}:{_text_on(c)};" for i, c in enumerate(PAL_L))
CSS = """
:root{--bg:#fff;--ink:#161616;--ink2:#4d4d4d;--rule:#dcdcdc;--track:#efe9f4;--accent:ACCENT_HEX;--stripe:#fff1f8;--other:#d9cfe6;--tother:#161616;/*SL*/color-scheme:light}
html,body{background:var(--bg)}
body{font:15px/1.45 Arial,Helvetica,"Liberation Sans",sans-serif;color:var(--ink);max-width:76rem;margin:1rem auto 1.5rem;padding:0 16px}
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:.1rem 1.5rem}
h1{font-size:1.5rem;font-weight:700;margin:0;color:var(--accent)}.asof{color:var(--ink2);font-size:.9rem}
.lede{margin:.1rem 0 .7rem}
.cols{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:0 2rem;align-items:stretch}.cols>div:first-child{display:flex;flex-direction:column}.cols>div:first-child>section:last-child{margin-top:auto}
@media (max-width:72rem){
.cols{grid-template-columns:auto minmax(0,1fr);grid-template-areas:"bars intro" "bars ran" "cost cost";gap:0 1.5rem;align-items:start}
.cols>div:first-child,.cols>div:last-child{display:contents}
.cols>div:first-child>section:first-child{grid-area:intro}.cols>div:first-child>section:last-child{grid-area:bars;margin-top:0}
.cols>div:last-child>section:first-child{grid-area:ran}.cols>div:last-child>section:last-child{grid-area:cost}
}
@media (max-width:68rem){.cols{grid-template-areas:"bars intro" "bars ." "ran ran" "cost cost"}}
@media (max-width:52rem){.cols{display:block}.cols>div:first-child,.cols>div:last-child{display:block}}
section{margin:0 0 .9rem}
h2{font-size:1rem;font-weight:700;margin:0 0 .3rem;padding-top:.4rem;border-top:1px solid var(--rule)}
p{margin:.35rem 0}.muted{color:var(--ink2);font-size:.86rem}.lim{margin:.8rem 0 .3rem}.desc{font-size:.86rem;margin:.2rem 0}
.warn{border-left:4px solid var(--accent);padding-left:.6rem;font-weight:700}
.meter{height:9px;background:var(--track)}.meter i{display:block;height:100%;background:var(--accent)}
.eq{overflow-x:auto;margin:.1rem 0 .1rem .6rem}.eq .katex-display{margin:.25rem 0;text-align:left}.eq .katex-display>.katex{text-align:left}.eq .katex{font-size:.98em}
details{margin:.3rem 0}summary{cursor:pointer;color:var(--ink2);font-size:.86rem}summary::marker{color:var(--accent)}details p{font-size:.86rem;color:var(--ink2)}
pre{background:var(--track);border-radius:4px;padding:.6rem .8rem;overflow-x:auto;font-size:.78rem;line-height:1.5;margin:.3rem 0}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.8rem}
th{text-align:left;font-weight:700;color:var(--ink2);border-bottom:2px solid var(--accent);padding:.2rem .45rem;vertical-align:bottom;font-size:.72rem}
th.n{text-align:right;white-space:normal}thead tr:first-child th.grp{border-bottom:1px solid var(--accent);text-align:left}th:first-child,td:first-child{white-space:nowrap}
td.good{color:#1a7f37;font-weight:bold}td.bad{color:#c62828;font-weight:bold}td{padding:.1rem .45rem;border-bottom:1px solid var(--rule)}tbody tr:nth-child(even) td{background:var(--stripe)}td+td,th+th{border-left:1px solid var(--rule)}td:first-child,th:first-child{padding-left:0}
.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}

/*CLS*/
.other{background:var(--other);color:var(--tother)}
.stack{display:flex;gap:0;height:64px;box-sizing:border-box;border:2px solid var(--ink);border-radius:5px;background:var(--bg);overflow:hidden;margin:.2rem 0 .8rem}.barlabel{display:flex;flex-direction:column;align-items:center;text-align:center;margin:.9rem 0 .3rem}.barlabel .bt{font-size:1.4rem;font-weight:700;line-height:1.15;white-space:nowrap}.barlabel .bs{font-size:.8rem;font-weight:400;white-space:nowrap;color:var(--ink2)}
.seg{border:0;padding:0;margin:0;font:inherit;display:flex;align-items:center;justify-content:center;position:relative;min-width:0}.stack button.seg{min-width:1px}
.seg.free{background:var(--bg)}.seg.free .lab{display:block;color:var(--ink);font-size:13px}
.lab{display:none;font:700 12px Arial,Helvetica,sans-serif;white-space:nowrap;pointer-events:none}
.seg.h .lab{display:block}.seg.v .lab{display:block;writing-mode:vertical-rl;transform:rotate(180deg)}
.seg.sel{outline:3px solid var(--ink);outline-offset:-3px;z-index:2}
button.seg:focus-visible{outline:2px dotted var(--ink);outline-offset:3px}
tr.pick.sel td{border-top:2px solid var(--ink);border-bottom:2px solid var(--ink)}
tr.pick.sel td:first-child{border-left:2px solid var(--ink)}tr.pick.sel td:last-child{border-right:2px solid var(--ink)}
tr.pick:focus-visible{outline:2px dotted var(--ink);outline-offset:-2px}
.sw{display:inline-block;width:10px;height:10px;margin-right:.45rem;border-radius:2px}
table.pt{font-size:.74rem}table.pt th{font-size:.72rem}table.pt td{padding:.1rem .5rem}table.pt td:first-child{padding-left:0}.pt thead tr:nth-child(2) th:first-child{padding-left:.5rem;border-left:1px solid var(--rule)}.pt td:nth-child(4),.pt thead tr:first-child th.grp:last-child,.pt thead tr:nth-child(2) th:nth-child(3){border-left-color:var(--ink2)}
.vbars{display:flex;flex-wrap:wrap;align-items:flex-start;gap:.3rem 1.5rem;padding-left:2.2rem}
.vcol{flex:1 1 240px;max-width:344px;min-width:0}.vcol .barlabel{margin:.9rem 0 .3rem}
.vw{overflow-x:auto}.vbars svg{display:block;width:100%;height:auto}
.vseg{outline:none}.vseg:focus-visible .segr{stroke:var(--ink);stroke-width:2;stroke-dasharray:3 2}.segr{stroke:none}.rem{fill:var(--bg)}
.tin{font:700 13px Arial,Helvetica,sans-serif;text-anchor:middle}.tin2{font:12px Arial,Helvetica,sans-serif;text-anchor:middle}.tin1{font:700 11.5px Arial,Helvetica,sans-serif;text-anchor:middle}
.olab{font:11px Arial,Helvetica,sans-serif;fill:var(--ink)}.lead{stroke:var(--ink2);stroke-width:1;fill:none}
.remt{font:700 14px Arial,Helvetica,sans-serif;fill:var(--ink);text-anchor:middle}.frame{fill:none;stroke:var(--ink);stroke-width:2}
.hl{display:none;fill:none;stroke:var(--ink);stroke-width:3;pointer-events:none}.hl.on{display:block}
.rd{color:var(--accent);font-weight:700}code{font-weight:700}pre code{font-weight:400}
@media (max-width:40rem){table{font-size:.76rem}td,th{padding-left:.35rem;padding-right:.35rem}.eq .katex{font-size:.88em}}
""".replace("ACCENT_HEX", ACCENT).replace("/*SL*/", SLOT_L).replace("/*CLS*/", CLS + CLSF)

# ---- "how are job costs calculated?" -----------------------------------------------------
# Only built (and only the ~200KB of KaTeX CSS only loaded) when there's an actual weighted
# billing model to explain. Otherwise the page shows the plain Slurm fields instead, in
# bin/build_usage_page.py's CODE_BLOCK below. No formula is invented for caps that aren't
# billing, since raw TRES caps (cpu/mem/node/GPU-hours on their own) aren't weighted at all.
EQ, EX1, EX2 = {}, "", ""
HAS_GPU_ANYWHERE = bool(gpu_parts)   # does any partition even have a GPU billing weight?
if HAS_COST_MODEL:
    def _tex_num(x): return f"{x:,.0f}".replace(",", "{,}")
    if HAS_GPU_ANYWHERE:
        _calc = rf"\begin{{array}}{{ll}}\text{{CPU hourly rate}} & = \text{{CPU cores}} \times \text{{Billing weight per core}}\\ \text{{RAM hourly rate}} & = \text{{RAM (GB)}} \times \text{{Billing weight per GB}}\\ \text{{GPU hourly rate}} & = \text{{Number of GPUs}} \times \text{{Billing weight per GPU}}\\[10pt] \text{{Hourly billing rate}} & = \max(\text{{CPU hourly rate}},\ \text{{RAM hourly rate}},\ \text{{GPU hourly rate}})\\[10pt] \color{{{ACCENT}}}\textbf{{Billing-hours}} & \color{{{ACCENT}}}\mathbf{{=}}\ \textbf{{Hourly billing rate}}\ \mathbf{{\times}}\ \textbf{{Time the job actually runs (in hours)}}\\ \color{{{ACCENT}}}\textbf{{GPU-hours}} & \color{{{ACCENT}}}\mathbf{{=}}\ \textbf{{Number of GPUs}}\ \mathbf{{\times}}\ \textbf{{Time the job actually runs (in hours)}}\end{{array}}"
    else:   # no partition here has a GPU billing weight at all, so don't mention GPUs in the formula
        _calc = rf"\begin{{array}}{{ll}}\text{{CPU hourly rate}} & = \text{{CPU cores}} \times \text{{Billing weight per core}}\\ \text{{RAM hourly rate}} & = \text{{RAM (GB)}} \times \text{{Billing weight per GB}}\\[10pt] \text{{Hourly billing rate}} & = \max(\text{{CPU hourly rate}},\ \text{{RAM hourly rate}})\\[10pt] \color{{{ACCENT}}}\textbf{{Billing-hours}} & \color{{{ACCENT}}}\mathbf{{=}}\ \textbf{{Hourly billing rate}}\ \mathbf{{\times}}\ \textbf{{Time the job actually runs (in hours)}}\end{{array}}"
    TEX = {"calc": _calc}
    _ex_part = gpu_parts[0] if gpu_parts else order[0]    # a GPU partition if one exists, otherwise any partition, to make the worked example concrete
    if _ex_part:
        wc, wm, wg = weights[_ex_part]; _c, _r, _g = REF_CORES * wc, REF_GB * wm, wg; _rate = max(_c, _r, _g if wg > 0 else 0)
        if wg > 0:
            TEX["run"] = rf"\begin{{array}}{{ll}}{_tex_num(_rate)} \times 4 & = {_tex_num(_rate * 4)}\ \text{{billing-hours}}\\ 1 \times 4 & = 4\ \text{{GPU-hours}}\end{{array}}"
            EX1 = (f"For example, a <code>{e(_ex_part)}</code> job with {REF_CORES} CPU cores, {REF_GB} GB of system RAM, and one GPU has hourly rates of {f0(_c)} for CPU, {f0(_r)} for RAM, and {f0(_g)} for the GPU.")
        else:
            TEX["run"] = rf"{_tex_num(_rate)} \times 4 = {_tex_num(_rate * 4)}\ \text{{billing-hours}}"
            EX1 = (f"For example, a <code>{e(_ex_part)}</code> job with {REF_CORES} CPU cores and {REF_GB} GB of system RAM has hourly rates of {f0(_c)} for CPU and {f0(_r)} for RAM.")
        EX2 = f"{CLUSTER_NAME} uses the highest rate: max({f0(_c)}, {f0(_r)}{f', {f0(_g)}' if wg > 0 else ''}) = {f0(_rate)}, rather than adding them together. If the job actually runs for 4 hours, it uses:"
    def render_tex(items):
        node = subprocess.run("command -v node", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True).stdout.strip() or "node"
        try:
            out = subprocess.run([node, os.path.join(HERE, "..", "math", "render_cli.js")], input=json.dumps(items), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=90, check=True, cwd=os.path.join(HERE, "..", "math")).stdout
            return json.loads(out)
        except Exception as ex:
            print("KaTeX render failed (is Node.js + `npm install` in math/ set up?), showing plain LaTeX instead:", ex); return [f'<div class="katex-display"><span>{html.escape(t)}</span></div>' for t in items]
    step("Typesetting equations...")
    EQ = dict(zip(TEX, render_tex(list(TEX.values()))))
else:
    # No billing cap and/or no TRESBillingWeights configured, so there's no weighted formula to
    # show, so the page shows exactly what it actually computes for each capped resource instead.
    def _ident(k): return re.sub(r"\W+", "_", k).strip("_") or "tres"
    CODE_BLOCK = "\n".join(
        f'{_ident(k)}_cap_hours  = GrpTRESMins["{k}"] / 60{"  / 1024" if k == "mem" else ""}   # sacctmgr show assoc where account={ACCOUNT} format=GrpTRESMins\n'
        f'{_ident(k)}_used_hours = GrpTRESRaw["{k}"]  / 60{"  / 1024" if k == "mem" else ""}   # sshare -A {ACCOUNT} -a  (Slurm\'s own decayed total; same number it uses to throttle new jobs)\n'
        f'{_ident(k)}_left_hours = {_ident(k)}_cap_hours - {_ident(k)}_used_hours\n'
        for k in CAP_KEYS
    ).rstrip()

VW, VH, BX, BW, LX, BAR_TOP, BAR_H = 344, 636, 2, 170, 192, 8, 620
def _pct(x): return "<0.1%" if 0 < x < 0.001 else pc(x)
def _spread(ys, gap, lo, hi):
    pos = list(ys)
    for i in range(1, len(pos)): pos[i] = max(pos[i], pos[i - 1] + gap)
    if pos and pos[-1] > hi:
        pos[-1] = hi
        for i in range(len(pos) - 2, -1, -1): pos[i] = min(pos[i], pos[i + 1] - gap)
    if pos and pos[0] < lo: pos = [p + lo - pos[0] for p in pos]
    return pos
def vbar_geom(key):
    cap_, left_, used_ = CAPS[key], LEFT[key], TOT.get(key, 0.0)
    title, unit = tres_info(key)
    fmt = f0 if cap_ >= 100 else f1
    segs = [(u, v.get(key, 0.0)) for u, v in named if v.get(key, 0.0) > 0]
    hs = [max(1.0, v / cap_ * BAR_H) for _, v in segs] if cap_ else [0.0 for _ in segs]
    if sum(hs) > BAR_H: hs = [h * BAR_H / sum(hs) for h in hs]
    y, items = BAR_TOP + BAR_H, []
    for (u, v), h in zip(segs, hs):
        y -= h; p2 = _pct(v / cap_) if cap_ else "n/a"; t2 = f"{fmt(v)} hrs, {p2}"
        if h >= 36 and 7.6 * len(u) <= BW - 12 and 6.6 * len(t2) <= BW - 12: mode = "in2"
        elif h >= 18 and 7.6 * len(f"{u}  {t2}") <= BW - 10: mode = "in1"
        else: mode = "out"
        items.append(dict(key=u, slot=slots[u], y=y, h=h, t2=t2, p2=p2, mode=mode, val=v))
    seen_out = False   # items are bottom-to-top, biggest-to-smallest; once one doesn't fit inside,
    for it in items:   # nothing smaller stacked above it should either: a bigger segment showing
        if seen_out: it["mode"] = "out"          # an outside label right below a smaller segment's
        elif it["mode"] == "out": seen_out = True  # inside label would look like an inconsistency
    outs = sorted([it for it in items if it["mode"] == "out"], key=lambda it: it["y"] + it["h"] / 2)
    for it, ly in zip(outs, _spread([it["y"] + it["h"] / 2 for it in outs], 15, BAR_TOP + 8, BAR_TOP + BAR_H - 8)): it["ly"] = ly
    return dict(title=title, unit=unit, fmt=fmt, items=items, zeros=[(u, slots[u]) for u, v in named if v.get(key, 0.0) <= 0],
                rem_h=BAR_H - sum(hs), rem_txt=f"{fmt(left_)} hrs left",
                sub=(f"{f0(left_)} left of {f0(cap_)} ({pc(used_ / cap_)} used)" if cap_ else "no cap set"))
def vbar_svg(g):
    cx, out = BX + BW / 2, []
    for it in g["items"]:
        k, sl, y0, h = e(it["key"]), it["slot"], it["y"], it["h"]; cy = y0 + h / 2
        a = f'{k}: {g["fmt"](it["val"])} {g["unit"]}, {it["p2"]} of the allowance'
        o = f'<g class="vseg" data-key="{k}" tabindex="0" role="button" aria-pressed="false" aria-label="{e(a)}"><rect class="segr f{sl}" x="{BX}" y="{y0:.2f}" width="{BW}" height="{h:.2f}"/>'
        if it["mode"] == "in2": o += f'<text class="tin tf{sl}" x="{cx}" y="{cy - 1:.1f}">{k}</text><text class="tin2 tf{sl}" x="{cx}" y="{cy + 14:.1f}">{e(it["t2"])}</text>'
        elif it["mode"] == "in1": o += f'<text class="tin1 tf{sl}" x="{cx}" y="{cy + 4:.1f}">{k}  {e(it["t2"])}</text>'
        else:
            ly = it["ly"]; o += f'<path class="lead" d="M{BX + BW} {cy:.1f} L{LX - 6} {ly:.1f}"/><rect class="f{sl}" x="{LX - 4}" y="{ly - 5:.1f}" width="9" height="9"/><text class="olab" x="{LX + 10}" y="{ly + 4:.1f}">{k}  {e(it["t2"])}</text>'
        out.append(o + "</g>")
    for n, (u, sl) in enumerate(g["zeros"]):
        ly = BAR_TOP + 8 + n * 16
        out.append(f'<g class="vseg" data-key="{e(u)}" tabindex="0" role="button" aria-pressed="false" aria-label="{e(u)}: 0 {g["unit"]}"><rect class="f{sl}" x="{LX - 4}" y="{ly - 5:.1f}" width="9" height="9"/><text class="olab" x="{LX + 10}" y="{ly + 4:.1f}">{e(u)}  0 hrs, 0%</text></g>')
    out.append(f'<rect class="rem" x="{BX}" y="{BAR_TOP}" width="{BW}" height="{g["rem_h"]:.2f}"/><text class="remt" x="{cx}" y="{BAR_TOP + g["rem_h"] / 2 + 6:.1f}">{e(g["rem_txt"])}</text>')
    out.append(f'<rect class="frame" x="{BX}" y="{BAR_TOP}" width="{BW}" height="{BAR_H}"/>')
    for it in g["items"]: out.append(f'<rect class="hl" data-key="{e(it["key"])}" x="{BX}" y="{it["y"]:.2f}" width="{BW}" height="{it["h"]:.2f}"/>')
    return f'<div class="vw"><svg viewBox="0 0 {VW} {VH}" role="group" aria-label="{e(g["title"])} used by each person, out of the whole allowance">{"".join(out)}</svg></div>'
BARS = [vbar_geom(k) for k in CAP_KEYS]      # one bar per capped resource, however many that is
def vbars_html():
    cols = "".join(f'<div class="vcol"><div class="barlabel"><span class="bt">{e(g["title"])}</span><span class="bs">{e(g["sub"])}</span></div>{vbar_svg(g)}</div>' for g in BARS)
    return f'<div class="vbars">{cols}</div>'
_tz = f" {TIMEZONE_LABEL}" if TIMEZONE_LABEL else ""
LEDE_LIVE = f"This page updates from {CLUSTER_NAME}'s records every day at {REFRESH_HOUR}{_tz}."
LEDE_SNAP = "This copy is a snapshot of the cluster's records from the date shown."
_titles = [tres_info(k)[0] for k in CAP_KEYS]
if len(_titles) == 1:
    LIMITS_SENTENCE = f"We have one limit for compute: {_titles[0]}."
else:
    LIMITS_SENTENCE = f"We have {len(_titles)} separate limits for compute ({', '.join(_titles[:-1])} and {_titles[-1]}). A job counts toward every one its resources touch."
TITLE = f"{LAB_NAME} {CLUSTER_NAME} Usage"
DECAY_SENTENCE = (f"Older usage counts less over time. With a {round(H / 24)}-day half-life, 100 {tres_info(PRIMARY)[1]} becomes 50 after {round(H / 24)} days, then 25 after another {round(H / 24)} days (unless a reset happens first)."
                  if DECAYS else "Usage does not fade over time on this cluster.")
def make_body(lede):
    if HAS_COST_MODEL:
        _eq_aria = "CPU, RAM and GPU hourly rates; hourly billing rate is the largest of the three; billing-hours and GPU-hours equations" if HAS_GPU_ANYWHERE else "CPU and RAM hourly rates; hourly billing rate is the larger of the two; billing-hours equation"
        _run_aria = "Worked example: billing-hours and GPU-hours for 4 hours" if HAS_GPU_ANYWHERE else "Worked example: billing-hours for 4 hours"
        _wait_sentence = "Time spent waiting in the queue is not counted in the time used to calculate billing-hours" + (" or GPU-hours." if HAS_GPU_ANYWHERE else ".")
        cost_calc_details = f"""<details><summary>How are job costs calculated?</summary>
<p>Hourly rates depend on the resources allocated to your job (even if your code doesn't fully use them up) and the partition's billing weights:</p>
<div class="eq" role="math" aria-label="{_eq_aria}">{EQ["calc"]}</div>
<p><i>{EX1}</i></p>
<p><i>{EX2}</i></p>
<div class="eq" role="math" aria-label="{_run_aria}">{EQ.get("run", "")}</div>
<p>{_wait_sentence}</p></details>"""
        _keep_running_sentence = ("The last column estimates how long that example job could run with what's left. For GPU jobs, whichever runs out first sets the limit: billing-hours or GPU-hours."
                                   if HAS_GPU_ANYWHERE else "The last column estimates how long that example job could run with the billing-hours left.")
        cost_section = f"""<section>
<h2>What a job costs now</h2>
<p class="desc">For context, the table below compares costs for a job with {REF_CORES} CPU cores and {REF_GB} GB of RAM{" (plus one GPU for GPU queues)" if HAS_GPU_ANYWHERE else ""}. Your job's cost may differ depending on the resources allocated to it.</p>
<div class="tw"><table><thead><tr><th>Partition</th><th class="n">Billing weight per core</th><th class="n">Billing weight per GB</th><th class="n">Billing weight per GPU</th><th class="n">Hourly billing rate</th><th class="n">Hours we could keep running</th></tr></thead><tbody>{cost_rows}</tbody></table></div>
<details><summary>How long could we keep running?</summary>
<p>{_keep_running_sentence}</p>
<p>This assumes only that job is running, with no future decreases or resets.</p></details>
</section>"""
    else:
        cost_calc_details = f"""<details><summary>How is this calculated?</summary>
<p>{CLUSTER_NAME} doesn't have a weighted billing model set up for this account, so there's no
single "cost" formula to show. Each limit above comes directly from these Slurm fields:</p>
<pre><code>{e(CODE_BLOCK)}</code></pre></details>"""
        cost_section = ""
    return f"""<header class="top"><h1>{e(TITLE)}</h1><span class="asof">Usage as of {now}</span></header>
<p class="lede"><i>{lede}</i></p>
<div class="cols">
<div>
<section>
<h2>How much compute do we have left?</h2>
<p class="desc">The whole lab shares one compute allowance.</p>
<p class="desc">{LIMITS_SENTENCE}</p>
{cost_calc_details}
{warn}<details><summary>Does usage reset?</summary>
<p>{DECAY_SENTENCE}</p>
<p>{CLUSTER_NAME} also lists a monthly reset. {RESET_SENTENCE}</p>
<p>If there isn't enough allowance left, new jobs may have to wait.</p></details>
</section>
<section>
{vbars_html()}
</section>
</div>
<div>
<section>
<h2>{RAN_TITLE}</h2>
<div class="tw"><table><thead><tr><th>Partition</th><th class="n">Jobs</th>{TRES_THS}<th class="n">Average job (hours)</th></tr></thead><tbody>{rows_parts}</tbody></table></div>
</section>
{cost_section}
</div>
</div>
<script>
(function(){{
  var els=[].slice.call(document.querySelectorAll('.vseg')), hls=[].slice.call(document.querySelectorAll('.hl')), sel=null;
  function apply(){{
    els.forEach(function(el){{ var on=sel!==null&&el.dataset.key===sel; el.setAttribute('aria-pressed',on?'true':'false'); }});
    hls.forEach(function(h){{ h.classList.toggle('on', sel!==null&&h.dataset.key===sel); }});
  }}
  function pick(k){{ sel=(sel===k)?null:k; apply(); }}
  els.forEach(function(el){{
    el.addEventListener('click',function(){{ pick(el.dataset.key); }});
    el.addEventListener('keydown',function(e){{ if(e.key==='Enter'||e.key===' '){{ e.preventDefault(); pick(el.dataset.key); }} }});
  }});
}})();
</script>"""
STYLE = CSS + KATEX_CSS
OUT_ART = OUT.replace("index.html", "index_artifact.html")
open(OUT, "w").write(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{e(TITLE)}</title><style>{STYLE}</style></head><body>{make_body(LEDE_LIVE)}</body></html>')
open(OUT_ART, "w").write(f"<title>{e(TITLE)}</title><style>{STYLE}</style>{make_body(LEDE_SNAP)}")
# a page with just the bars, screenshotted into bars.png for the optional Slack post (post_slack.py)
open(os.path.join(ROOT, "bars_only.html"), "w").write(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex,nofollow"><title>{e(TITLE)}</title><style>{STYLE}body{{margin:0;padding:.8rem 1rem;max-width:none}}</style></head><body><h1 style="margin:0 0 .5rem 2.2rem;font-size:1.2rem">{e(TITLE)}, {now}</h1>{vbars_html()}</body></html>')
_summary = ", ".join(f"{f0(TOT.get(k, 0))}/{f0(CAPS[k])} {tres_info(k)[1]}" for k in CAP_KEYS)
print("wrote", OUT, "and", OUT_ART, f"({os.path.getsize(OUT) // 1024} KB)"); print(f"used {_summary}; users {len(users)}, partitions {len(parts)}")
