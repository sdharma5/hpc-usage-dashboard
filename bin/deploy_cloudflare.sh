#!/bin/bash
# Upload index.html to Cloudflare Pages (an unlisted link, kept out of search engines).
# Does nothing until ~/.config/<APP_SLUG>/cloudflare.env exists (setup.sh writes it) with:
#   CLOUDFLARE_API_TOKEN=...   (a token scoped to "Cloudflare Pages: Edit", nothing else)
#   CLOUDFLARE_ACCOUNT_ID=...
# CF_PROJECT (the site becomes https://<CF_PROJECT>.pages.dev) comes from config.env.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
[ -f "$ROOT/config.env" ] || { echo "no config.env: run ./setup.sh first"; exit 1; }
set -a; . "$ROOT/config.env"; set +a
APP_SLUG="${APP_SLUG:-hpc-usage}"
[ -n "$CF_PROJECT" ] || { echo "CF_PROJECT is not set in config.env; skipping Cloudflare upload"; exit 0; }
CFG="$HOME/.config/$APP_SLUG/cloudflare.env"
[ -f "$CFG" ] || { echo "upload skipped: $CFG not found"; exit 0; }
[ "$(stat -c %a "$CFG" 2>/dev/null || stat -f %Lp "$CFG")" = "600" ] || { echo "upload stopped: run  chmod 600 $CFG  (it holds a token)"; exit 1; }
set -a; . "$CFG"; set +a
for V in CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID; do [ -n "${!V}" ] || { echo "upload stopped: $V is missing from $CFG"; exit 1; }; done
export CI=1 WRANGLER_SEND_METRICS=false
WRANGLER="wrangler"; command -v wrangler >/dev/null 2>&1 || WRANGLER="npx --yes wrangler@3"
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
cp "$ROOT/index.html" "$STAGE/index.html"
printf '/*\n  X-Robots-Tag: noindex, nofollow\n' > "$STAGE/_headers"
printf 'User-agent: *\nDisallow: /\n' > "$STAGE/robots.txt"
# first run only: create the project (the error when it already exists is expected and harmless)
$WRANGLER pages project create "$CF_PROJECT" --production-branch main >/dev/null 2>&1 || true
OUT=$($WRANGLER pages deploy "$STAGE" --project-name "$CF_PROJECT" --branch main --commit-dirty=true 2>&1); RC=$?
echo "$OUT" | sed "s/$CLOUDFLARE_API_TOKEN/***/g" | tail -12
if [ $RC -ne 0 ]; then echo "UPLOAD FAILED (exit $RC). The page on the cluster is still updated; the online copy was not."; exit $RC; fi
echo "uploaded. link: https://$CF_PROJECT.pages.dev"
