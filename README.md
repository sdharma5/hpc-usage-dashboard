# hpc-usage-dashboard

A plain, static usage page for a shared Slurm allocation: how much compute is left, who's used
what, what ran recently, and what a job costs on each partition. No JavaScript framework, no
backend, no database — it's a page a `sbatch` job regenerates from Slurm's own accounting every
morning, that you can host anywhere (a free Cloudflare Pages link works well) and optionally post
a picture of to Slack.

Built for one specific lab's Slurm cluster first; this is the general version, meant to be reused
by any lab on any Slurm cluster.

![screenshot placeholder — see index.html after setup](docs/screenshot.png)

## What it shows

- **Allowance left**, as two bars (billing-hours and GPU-hours), each broken down by person.
  Colors are generated from one accent color you pick, so every lab looks different but
  consistent.
- **What we ran since the last reset** (or the past 30 days, if the cluster never resets usage),
  by partition: jobs, billing-hours, GPU-hours, average job length.
- **What a job costs now**: for a reference job size you choose, the billing rate and how long
  you could keep running it on each partition — with the cheapest and priciest GPU partitions
  colored green-to-red.
- Plain-language explanations of how billing-hours/GPU-hours are calculated and whether/when
  usage resets, with the equations typeset (via KaTeX, if Node.js is available; falls back to
  plain text otherwise).

Clicking a person's name or color highlights them in both bars and the table.

## What it needs from your cluster

This assumes a fairly common Slurm setup:

- An **account** (allocation) with `GrpTRESMins` caps for `billing` and (optionally) `gres/gpu`
  minutes — i.e. `sacctmgr show assoc where account=<acct> format=GrpTRESMins` returns something.
- `TRESBillingWeights` configured on at least the partitions you care about
  (`scontrol show partition` shows `TRESBillingWeights=...`), so a job's hourly rate is
  `max(cores × weight, GB × weight, gpus × weight)`.
- Read access to `sacctmgr`, `sshare`, `sacct` and `scontrol` for that account. No special
  privileges — this is the same information `sshare -A <acct>` shows you already.
- Python 3, and a Slurm login/submit node to run the daily `sbatch` job from.

If your cluster doesn't use `TRESBillingWeights` or `GrpTRESMins`, this tool isn't a fit as-is —
the numbers it shows come directly from those.

Optional, not required:
- **Node.js** (any recent version) — lets equations render as typeset math (KaTeX) instead of
  plain LaTeX text.
- **Firefox or Chrome/Chromium** (headless) — only needed for the optional Slack post, to
  screenshot the two bars as a PNG.
- **A free Cloudflare account** — for hosting the page at a public-but-unlisted link. You can
  skip this and just look at the local `index.html`, or host it some other way.
- **A Slack app/bot** — for the optional daily post of the usage bars into a channel.

## Setup

```
git clone <this repo> hpc-usage-dashboard
cd hpc-usage-dashboard
./setup.sh
```

The wizard asks about your lab, cluster, Slurm account, colors, and (optionally) walks you
through Cloudflare Pages hosting and a Slack bot post. It writes `config.env` (see
`config.example.env` for every setting and what it does — no secrets go in this file), generates
`refresh_daily.sbatch` for your cluster, and offers to build the page and submit the daily job
right away. It's safe to re-run any time to change your answers.

Credentials never go in the repo. They're saved outside it, in
`~/.config/<APP_SLUG>/{cloudflare,slack}.env`, each `chmod 600`.

### Manual setup (no wizard)

```
cp config.example.env config.env    # edit it
mkdir -p logs
sed -e "s|@@APP_SLUG@@|myapp|g" -e "s|@@ACCOUNT@@|myacct|g" -e "s|@@PARTITION@@|general|g" \
    -e "s|@@ROOT@@|$PWD|g" -e "s|@@REFRESH_HOUR@@|06:00|g" -e "s|@@PYTHON@@|$(command -v python3)|g" \
    bin/refresh_daily.sbatch.template > refresh_daily.sbatch
python3 bin/build_usage_page.py     # builds index.html once, to check it
sbatch refresh_daily.sbatch         # runs now, then re-schedules itself daily
```

## How the daily refresh works

Most shared clusters disable per-user `cron`/`scrontab`, so `refresh_daily.sbatch` re-submits
itself: each run queues the *next* day's run first (only if one isn't already waiting), then
rebuilds the page, uploads it to Cloudflare Pages if configured, and posts to Slack if configured.
Queuing next-before-doing-anything-else means a failed rebuild can never break the daily chain.

Check it's running: `squeue -u $USER -n <APP_SLUG>-daily`
Stop it: `scancel -u $USER -n <APP_SLUG>-daily` (cancels the waiting future run; nothing else to
clean up)

## How the numbers are computed

- **Caps**: `sacctmgr show assoc ... format=GrpTRESMins`, converted from minutes to hours.
- **Current usage**: `sshare -A <acct> -a` — Slurm's own decayed usage total per person. This is
  the same number Slurm itself uses to throttle new jobs, so it's authoritative, not a guess.
- **Reset detection**: your cluster may reset usage on a schedule (`PriorityUsageResetPeriod`).
  There's no direct way to ask Slurm "when was the last reset", so the page works it back out:
  it rebuilds the account's total from job records for every possible "started counting here"
  date over the last 100 days (fading old jobs by the cluster's own configured decay half-life,
  read live from `scontrol show config`), and picks whichever date reproduces Slurm's own current
  total most closely. It only reports a reset if one date fits much better than assuming no reset
  at all — otherwise it says nothing, rather than guessing. This is inferred, not authoritative;
  the page says so, and you should still confirm the real reset schedule with your cluster admins
  if it matters.
- **Job cost table**: `scontrol show partition` for `TRESBillingWeights`, applied to the
  reference job size you chose in setup, using Slurm's own `max(...)` billing rule.

## Customizing

- **Colors**: set `ACCENT` in `config.env`; the rest of the per-person palette is generated
  from it automatically (each person still gets a fixed, stable color across days —
  `user_colors.json`, regenerated, not hand-edited).
- **Reference job size**, **refresh time**, **account**, **partition names shown**: all in
  `config.env`; re-run `./setup.sh` or edit the file and rebuild.
- **Wording**: `bin/build_usage_page.py` is one file of plain Python generating HTML strings —
  no templating engine, so any sentence on the page is a plain string you can search for and
  change directly.

## Files

```
config.example.env             every setting, documented (copy to config.env)
setup.sh                       interactive setup wizard
bin/build_usage_page.py        builds index.html / index_artifact.html / bars_only.html
bin/deploy_cloudflare.sh       uploads index.html to Cloudflare Pages (skips itself if unset)
bin/post_slack.py              posts bars_only.html as a PNG to Slack (skips itself if unset)
bin/refresh_daily.sbatch.template   filled in by setup.sh -> refresh_daily.sbatch
math/                          KaTeX (render_cli.js + its CSS); `npm install` here for KaTeX
docs/SLACK_SETUP.md            step-by-step Slack app setup
docs/CLOUDFLARE_SETUP.md       step-by-step Cloudflare Pages setup
```

After setup, your installation also has (all git-ignored, all local/generated):
`config.env`, `refresh_daily.sbatch`, `index.html`, `index_artifact.html`, `bars_only.html`,
`bars.png`, `user_colors.json`, `slack_last_file.json`, `logs/`.

## License

MIT — see `LICENSE`.
