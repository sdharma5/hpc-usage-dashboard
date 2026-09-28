#!/usr/bin/env python3
"""Screenshot the two bars (bars_only.html -> bars.png) and post the PNG to a Slack channel as a
bot. Each run also deletes the previous run's whole message (text + image) once the new one is
posted, so the channel only ever shows the latest one.
Needs config.env (SLACK_ENABLED=true, CF_PROJECT or PUBLIC_URL) and secrets in
~/.config/<APP_SLUG>/slack.env (mode 600) with SLACK_BOT_TOKEN=... and SLACK_CHANNEL_ID=...
(the bot needs the files:write and chat:write scopes, and must be invited to the channel — see
docs/SLACK_SETUP.md; setup.sh writes this file for you). The token is never printed. Exits 0 with
a message, doing nothing, if Slack isn't configured, so the daily job never breaks over this."""
import os, sys, json, stat, subprocess, shutil, time, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)

def load_config():
    cfg = {}
    path = os.path.join(ROOT, "config.env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1); cfg[k.strip()] = v.strip().strip("'\"")
    for k in list(cfg):
        if k in os.environ: cfg[k] = os.environ[k]
    return cfg
CFG = load_config()
def cfg(key, default=""): return os.environ.get(key, CFG.get(key, default))

if cfg("SLACK_ENABLED", "false").lower() not in ("1", "true", "yes"):
    print("slack: disabled in config.env, skipping"); sys.exit(0)

APP_SLUG = cfg("APP_SLUG", "hpc-usage")
LINK = cfg("PUBLIC_URL") or (f"https://{cfg('CF_PROJECT')}.pages.dev" if cfg("CF_PROJECT") else "")
CONF = os.path.expanduser(f"~/.config/{APP_SLUG}/slack.env"); PNG = os.path.join(ROOT, "bars.png")
if not os.path.exists(CONF): print(f"slack: no config at {CONF} yet, skipping"); sys.exit(0)
if stat.S_IMODE(os.stat(CONF).st_mode) & 0o077: print(f"slack: config must be chmod 600, skipping"); sys.exit(1)
cfg2 = dict(l.strip().split("=", 1) for l in open(CONF) if "=" in l and not l.lstrip().startswith("#")); tok = cfg2.get("SLACK_BOT_TOKEN", "").strip("'\""); ch = cfg2.get("SLACK_CHANNEL_ID", "").strip("'\"")
if not tok or not ch: print("slack: token or channel missing in config, skipping"); sys.exit(1)

BROWSER = next((b for b in ("firefox", "google-chrome", "chromium", "chromium-browser") if shutil.which(b)), None)
if not BROWSER: print("slack: no headless browser (firefox/chrome) found, skipping screenshot"); sys.exit(0)
if os.path.exists(PNG): os.remove(PNG)
if BROWSER == "firefox":
    subprocess.run([BROWSER, "--headless", "--screenshot", PNG, "--window-size=800,760", f"file://{ROOT}/bars_only.html"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
else:
    subprocess.run([BROWSER, "--headless=new", "--disable-gpu", f"--screenshot={PNG}", "--window-size=800,760", f"file://{ROOT}/bars_only.html"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
if not os.path.exists(PNG) or os.path.getsize(PNG) < 5000: print("slack: screenshot failed"); sys.exit(1)

def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}); return urllib.request.urlopen(req, timeout=60).read()
def api(method, **kw):
    r = json.loads(call(f"https://slack.com/api/{method}", urllib.parse.urlencode(kw).encode(), {"Authorization": f"Bearer {tok}"}))
    if not r.get("ok"): print(f"slack: {method} failed: {r.get('error')}"); sys.exit(1)
    return r
def post_ok(method, **kw):      # best effort: a failure here must not stop the run
    try: return json.loads(call(f"https://slack.com/api/{method}", urllib.parse.urlencode(kw).encode(), {"Authorization": f"Bearer {tok}"}))
    except Exception as ex: return {"ok": False, "error": str(ex)}

data = open(PNG, "rb").read(); up = api("files.getUploadURLExternal", filename="usage.png", length=len(data))
call(up["upload_url"], data, {"Content-Type": "image/png"})
STATE = os.path.join(ROOT, "slack_last_file.json")
old = json.load(open(STATE)) if os.path.exists(STATE) else None
text = f"Usage this morning." + (f" Full page: {LINK}" if LINK else "")
# upload the file without sharing it, then post one message of our own that shows it; chat.postMessage
# hands back the message id, so tomorrow's run can delete exactly that message
api("files.completeUploadExternal", files=json.dumps([{"id": up["file_id"], "title": "Usage"}]))
blocks = json.dumps([{"type": "section", "text": {"type": "mrkdwn", "text": text}}, {"type": "image", "slack_file": {"id": up["file_id"]}, "alt_text": "Usage: billing-hours and GPU-hours bars"}])
for _ in range(6):
    r = post_ok("chat.postMessage", channel=ch, text=text, blocks=blocks)
    if r.get("ok"): break
    time.sleep(3)
if not r.get("ok"): print("slack: post failed:", r.get("error")); sys.exit(1)
json.dump({"file_id": up["file_id"], "msgs": [(r["channel"], r["ts"])]}, open(STATE, "w")); print("slack: posted")
if old:        # today's post is up, now remove yesterday's message and file (the message must go first, or Slack leaves a "This file was deleted" stub)
    n = sum(1 for c, ts in old.get("msgs", []) if post_ok("chat.delete", channel=c, ts=ts).get("ok"))
    r = post_ok("files.delete", file=old["file_id"]); print(f"slack: removed yesterday's message ({n} of {len(old.get('msgs', []))}) and file ({'ok' if r.get('ok') else r.get('error')})")
