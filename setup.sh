#!/bin/bash
# Interactive setup wizard for hpc-usage-dashboard. Run once per installation:
#   ./setup.sh
# It asks a few questions, writes config.env, optionally sets up Cloudflare Pages hosting and a
# Slack bot post, generates refresh_daily.sbatch, and (if you say yes) submits the first run.
# Safe to re-run any time: it shows your current answers as defaults.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ask() {   # ask "prompt" "default" -> prints the answer (default kept if user hits Enter)
  local prompt="$1" default="$2" ans
  if [ -n "$default" ]; then read -rp "$prompt [$default]: " ans; else read -rp "$prompt: " ans; fi
  echo "${ans:-$default}"
}
yesno() {  # yesno "prompt" "y|n" -> "true" or "false"
  local prompt="$1" default="$2" ans
  read -rp "$prompt [$([ "$default" = y ] && echo Y/n || echo y/N)]: " ans
  ans="${ans:-$default}"
  case "$ans" in y|Y|yes|Yes) echo true;; *) echo false;; esac
}

# ---- load any existing config.env as defaults, so re-running the wizard is non-destructive ---
[ -f config.env ] && { set -a; . ./config.env; set +a; }

bold "== hpc-usage-dashboard setup =="
echo "A public HPC usage page for one Slurm account: allowance left, per-person usage, what ran"
echo "recently, and what a job costs. See README.md for what this needs from your cluster."
echo

command -v sacctmgr >/dev/null 2>&1 || echo "warning: sacctmgr not found on PATH: is this running on a Slurm login node?"
PYTHON3="$(command -v python3 || true)"
[ -n "$PYTHON3" ] || { echo "python3 is required and was not found on PATH."; exit 1; }

bold "-- Identity --"
LAB_NAME="$(ask "What's your lab or group called?" "${LAB_NAME:-My Lab}")"
CLUSTER_NAME="$(ask "What's your cluster called?" "${CLUSTER_NAME:-MyCluster}")"
DEFAULT_SLUG="$(echo "${APP_SLUG:-$CLUSTER_NAME-usage}" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')"
APP_SLUG="$(ask "Pick a short name with no spaces for this setup, e.g. \"skipjack-usage\" (used to name the daily job and a settings folder)" "$DEFAULT_SLUG")"

bold "-- Slurm --"
ACCOUNT="$(ask "Which Slurm account (allocation) should this track? Usually your lab's or PI's account (ex. JHED)." "${ACCOUNT:-$(id -gn)}")"
if command -v sacctmgr >/dev/null 2>&1; then
  if ! sacctmgr -n show account "$ACCOUNT" >/dev/null 2>&1; then
    echo "warning: 'sacctmgr show account $ACCOUNT' found nothing, double check the name."
  elif [ -z "$(sacctmgr -n show assoc where account="$ACCOUNT" user= 2>/dev/null)" ]; then
    echo "warning: '$ACCOUNT' exists as an account, but has no association with its own caps set"
    echo "(sacctmgr show assoc where account=$ACCOUNT user= found nothing). This is often a"
    echo "storage/scratch allocation name rather than a compute account -- double check the name."
  fi
fi
DEFAULT_PARTITION="${PARTITION:-}"
if command -v sinfo >/dev/null 2>&1; then
  echo "Partitions on this cluster: $(sinfo -h -o '%P' | tr '\n' ' ')"
  [ -n "$DEFAULT_PARTITION" ] || DEFAULT_PARTITION="$(sinfo -h -o '%P' | grep '\*$' | head -1 | tr -d '*')"   # the one sinfo marks default, if any
fi
while :; do
  PARTITION="$(ask "Which partition should the daily refresh job itself run on? (It only needs 1 CPU core, 1 GB of RAM, and about a minute.)" "$DEFAULT_PARTITION")"
  [ -n "$PARTITION" ] && break
  echo "A partition name is required: the daily job can't be submitted without one."
done
REFRESH_HOUR="$(ask "What time should the page refresh each day? (24-hour HH:MM)" "${REFRESH_HOUR:-06:00}")"
if ! [[ "$REFRESH_HOUR" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then echo "That doesn't look like HH:MM, using 06:00."; REFRESH_HOUR=06:00; fi
TIMEZONE_LABEL="$(ask "Timezone (ET, CT, MT, PT, GMT, UTC, or your own)" "${TIMEZONE_LABEL:-$(date +%Z)}")"
REF_CORES="$(ask "The page shows an example job's cost to illustrate pricing. How many CPU cores should that example job use?" "${REF_CORES:-8}")"
REF_GB="$(ask "And how much RAM (in GB) should that example job use?" "${REF_GB:-64}")"

bold "-- Look --"
ACCENT="$(ask "Pick an accent color for the page (hex code, e.g. #2563eb)." "${ACCENT:-#2563eb}")"
if ! [[ "$ACCENT" =~ ^#[0-9a-fA-F]{6}$ ]]; then echo "That doesn't look like #rrggbb, using #2563eb."; ACCENT=#2563eb; fi
PINNED_USER="${PINNED_USER:-}"

bold "-- Hosting (optional): Cloudflare Pages --"
echo "Publishes the page at an unlisted https://<name>.pages.dev link, free, updated by the daily job."
CF_ENABLE="$(yesno "Want to publish this page online for free with Cloudflare Pages?" "$([ -n "$CF_PROJECT" ] && echo y || echo n)")"
if [ "$CF_ENABLE" = true ]; then
  SUFFIX="$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  CF_PROJECT="$(ask "Cloudflare Pages project name (site = https://<this>.pages.dev)" "${CF_PROJECT:-$APP_SLUG-$SUFFIX}")"
  echo
  echo "You'll need, from https://dash.cloudflare.com :"
  echo "  1. Your Account ID (right sidebar of any Cloudflare dashboard page)."
  echo "  2. An API token: My Profile > API Tokens > Create Token > Edit Cloudflare Workers"
  echo "     template, or a custom token scoped to 'Cloudflare Pages: Edit' for your account."
  echo "See docs/CLOUDFLARE_SETUP.md for screenshots-by-description."
  echo
  read -rp "Cloudflare Account ID: " CF_ACCOUNT_ID
  read -rsp "Cloudflare API token (hidden; paste, then Enter): " CF_TOKEN; echo
  if [ -n "$CF_ACCOUNT_ID" ] && [ -n "$CF_TOKEN" ]; then
    mkdir -p "$HOME/.config/$APP_SLUG"; umask 077
    printf 'CLOUDFLARE_API_TOKEN=%s\nCLOUDFLARE_ACCOUNT_ID=%s\n' "$CF_TOKEN" "$CF_ACCOUNT_ID" > "$HOME/.config/$APP_SLUG/cloudflare.env"
    chmod 600 "$HOME/.config/$APP_SLUG/cloudflare.env"
    echo "saved to ~/.config/$APP_SLUG/cloudflare.env"
  else
    echo "skipped: nothing saved. CF_PROJECT is still set in config.env; add the credentials later to enable uploads."
  fi
else
  CF_PROJECT=""
fi

bold "-- Optional: post a picture of the usage bars to Slack every morning --"
SLACK_ENABLE="$(yesno "Want it to also post a picture of the usage bars to Slack every morning?" "$([ "${SLACK_ENABLED:-false}" = true ] && echo y || echo n)")"
if [ "$SLACK_ENABLE" = true ]; then
  echo
  echo "You'll need a Slack app with a bot token. See docs/SLACK_SETUP.md for the full walkthrough:"
  echo "  1. https://api.slack.com/apps -> Create New App -> From scratch."
  echo "  2. OAuth & Permissions -> Bot Token Scopes -> add files:write and chat:write."
  echo "  3. Install to Workspace; copy the Bot User OAuth Token (starts with xoxb-)."
  echo "  4. In the target Slack channel: /invite @<your app's bot name>."
  echo "  5. The channel ID (starts with C): right-click the channel -> View channel details."
  echo
  read -rsp "Slack bot token (hidden; paste, then Enter): " SLACK_TOKEN; echo
  read -rp "Slack channel ID (starts with C): " SLACK_CHANNEL
  if [ -n "$SLACK_TOKEN" ] && [ -n "$SLACK_CHANNEL" ]; then
    mkdir -p "$HOME/.config/$APP_SLUG"; umask 077
    printf 'SLACK_BOT_TOKEN=%s\nSLACK_CHANNEL_ID=%s\n' "$SLACK_TOKEN" "$SLACK_CHANNEL" > "$HOME/.config/$APP_SLUG/slack.env"
    chmod 600 "$HOME/.config/$APP_SLUG/slack.env"
    echo "saved to ~/.config/$APP_SLUG/slack.env"
    SLACK_ENABLED=true
  else
    echo "skipped: nothing saved."; SLACK_ENABLED=false
  fi
  if ! command -v firefox >/dev/null 2>&1 && ! command -v google-chrome >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1; then
    echo "note: no headless firefox/chrome found on PATH: the Slack post needs one of these to screenshot the bars. Install one, or the daily job will just skip the Slack step."
  fi
else
  SLACK_ENABLED=false
fi

bold "-- Writing config.env --"
cat > config.env <<CFGEOF
# Generated by setup.sh on $(date +%F). Edit by hand or re-run ./setup.sh. No secrets in this
# file: see config.example.env for what each setting does. Values are quoted because some
# scripts source this file directly with bash; keep quoting any value that has a space in it.
LAB_NAME="$LAB_NAME"
CLUSTER_NAME="$CLUSTER_NAME"
APP_SLUG="$APP_SLUG"
ACCOUNT="$ACCOUNT"
PARTITION="$PARTITION"
REFRESH_HOUR="$REFRESH_HOUR"
TIMEZONE_LABEL="$TIMEZONE_LABEL"
REF_CORES="$REF_CORES"
REF_GB="$REF_GB"
ACCENT="$ACCENT"
PINNED_USER="$PINNED_USER"
CF_PROJECT="$CF_PROJECT"
PUBLIC_URL="${PUBLIC_URL:-}"
SLACK_ENABLED="$SLACK_ENABLED"
CFGEOF
echo "wrote $ROOT/config.env"

bold "-- Math rendering (optional) --"
if command -v node >/dev/null 2>&1; then
  if [ ! -d math/node_modules ]; then
    echo "Node.js found: installing KaTeX for nicely-typeset equations (npm install in math/)..."
    (cd math && npm install --no-audit --no-fund --silent) && echo "done." || echo "npm install failed: equations will fall back to plain text; the page still works."
  fi
else
  echo "No Node.js found: equations will show as plain LaTeX text instead of typeset math."
  echo "Install Node.js and re-run this script to enable KaTeX rendering."
fi

mkdir -p logs
bold "-- Generating refresh_daily.sbatch --"
sed -e "s|@@APP_SLUG@@|$APP_SLUG|g" -e "s|@@ACCOUNT@@|$ACCOUNT|g" -e "s|@@PARTITION@@|$PARTITION|g" \
    -e "s|@@ROOT@@|$ROOT|g" -e "s|@@REFRESH_HOUR@@|$REFRESH_HOUR|g" -e "s|@@PYTHON@@|$PYTHON3|g" \
    bin/refresh_daily.sbatch.template > refresh_daily.sbatch
chmod +x refresh_daily.sbatch bin/*.sh bin/*.py
echo "wrote $ROOT/refresh_daily.sbatch"

echo
RUN_NOW="$(yesno "Build the page once now, to check everything works?" y)"
if [ "$RUN_NOW" = true ]; then
  "$PYTHON3" bin/build_usage_page.py && echo "-> open $ROOT/index.html in a browser to look at it."
fi

if command -v sbatch >/dev/null 2>&1; then
  SUBMIT="$(yesno "Submit the daily job now (runs immediately, then re-schedules itself for $REFRESH_HOUR $TIMEZONE_LABEL each day)?" y)"
  if [ "$SUBMIT" = true ]; then
    sbatch refresh_daily.sbatch
    echo "submitted. check with:  squeue -u \$USER -n $APP_SLUG-daily"
  fi
else
  echo "sbatch not found. Submit refresh_daily.sbatch yourself once Slurm is available: sbatch refresh_daily.sbatch"
fi

echo
bold "== done =="
echo "Config:        $ROOT/config.env"
echo "Page:          $ROOT/index.html"
[ -n "$CF_PROJECT" ] && echo "Online (once Cloudflare credentials are saved): https://$CF_PROJECT.pages.dev"
echo "Daily job:     $APP_SLUG-daily   (squeue -n $APP_SLUG-daily   /   scancel -n $APP_SLUG-daily to stop)"
echo "Re-run ./setup.sh any time to change these answers."
