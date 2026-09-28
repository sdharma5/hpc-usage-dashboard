# hpc-usage-dashboard

A plain static usage page for a shared Slurm allocation. Shows how much compute is left, who's
used what, what ran recently, and what a job costs on each partition. No framework, no backend,
no database. A daily `sbatch` job rebuilds it from Slurm's own accounting, you host it wherever
you want (a free Cloudflare Pages link works fine), and it can optionally post a picture of the
usage bars to Slack every morning.

Built first for one lab's cluster. This is the general version, so any lab on any Slurm cluster
can use it.

## What it shows

- Allowance left, as two bars (billing-hours and GPU-hours), broken down by person. Colors are
  generated from one accent color you pick, so every lab can look different but still consistent.
- What ran since the last reset (or the past 30 days if the cluster doesn't reset usage),
  by partition: jobs, billing-hours, GPU-hours, average job length.
- What a job costs right now: for a reference job size you pick, the billing rate and how long
  you could keep running it on each partition. Cheapest and priciest GPU partitions are colored
  green to red.
- Plain explanations of how billing-hours and GPU-hours are calculated, and whether usage resets,
  with the equations typeset if Node.js is available, plain text otherwise.

Click a person's name or color and it highlights them in both bars and the table.

## What it needs from your cluster

This assumes a pretty standard setup:

- An account with `GrpTRESMins` caps for billing and (optionally) GPU minutes, i.e.
  `sacctmgr show assoc where account=<acct> format=GrpTRESMins` returns something.
- `TRESBillingWeights` configured on at least the partitions you care about
  (`scontrol show partition` shows `TRESBillingWeights=...`), so a job's hourly rate is
  `max(cores x weight, GB x weight, gpus x weight)`.
- Read access to `sacctmgr`, `sshare`, `sacct` and `scontrol` for that account. Nothing special,
  it's the same info `sshare -A <acct>` already shows you.
- Python 3 and a login/submit node to run the daily job from.

If your cluster doesn't use `TRESBillingWeights` or `GrpTRESMins`, this isn't going to fit as is.
The numbers come straight from those.

Optional, not required:
- Node.js, any recent version, for typeset equations (KaTeX) instead of plain LaTeX text.
- Firefox or Chrome/Chromium, headless, only needed for the Slack post (screenshots the bars).
- A free Cloudflare account, for hosting at a public but unlisted link. You can skip this and
  just use the local `index.html`, or host it some other way.
- A Slack app/bot, for the daily post into a channel.

## Setup

```
git clone <this repo> hpc-usage-dashboard
cd hpc-usage-dashboard
./setup.sh
```

The wizard asks about your lab, cluster, account, colors, and optionally walks through Cloudflare
Pages and a Slack bot. It writes `config.env` (see `config.example.env` for what each setting
does, no secrets in that file), generates `refresh_daily.sbatch` for your cluster, and can build
the page and submit the daily job right away. Re-run it any time to change an answer.

Credentials never go in the repo. They're saved outside it, in
`~/.config/<APP_SLUG>/{cloudflare,slack}.env`, each chmod 600.

### Doing it by hand instead

```
cp config.example.env config.env    # edit it
mkdir -p logs
sed -e "s|@@APP_SLUG@@|myapp|g" -e "s|@@ACCOUNT@@|myacct|g" -e "s|@@PARTITION@@|general|g" \
    -e "s|@@ROOT@@|$PWD|g" -e "s|@@REFRESH_HOUR@@|06:00|g" -e "s|@@PYTHON@@|$(command -v python3)|g" \
    bin/refresh_daily.sbatch.template > refresh_daily.sbatch
python3 bin/build_usage_page.py     # builds index.html once so you can check it
sbatch refresh_daily.sbatch         # runs now, then re-schedules itself daily
```

## How the daily refresh works

Most shared clusters don't let you run your own cron or scrontab, so `refresh_daily.sbatch`
resubmits itself. Each run queues tomorrow's run first (only if one isn't already waiting), then
rebuilds the page, uploads it to Cloudflare if that's set up, and posts to Slack if that's set up.
Queuing the next run before doing anything else means a failed rebuild can't break the daily
chain.

Check it's running: `squeue -u $USER -n <APP_SLUG>-daily`

Stop it: `scancel -u $USER -n <APP_SLUG>-daily` (cancels the waiting future run, nothing else to
clean up)

## How the numbers are computed

- Caps: `sacctmgr show assoc ... format=GrpTRESMins`, converted from minutes to hours.
- Current usage: `sshare -A <acct> -a`, Slurm's own decayed usage total per person. This is the
  same number Slurm uses to throttle new jobs, so it's not a guess.
- Reset detection: your cluster might reset usage on a schedule (`PriorityUsageResetPeriod`).
  There's no direct way to ask Slurm when the last reset happened, so the page works it out: it
  rebuilds the account's total from job records for every possible start date over the last 100
  days (fading old jobs by the cluster's own decay half-life, read live from
  `scontrol show config`), and picks whichever date reproduces Slurm's current total closest. It
  only reports a reset if one date fits a lot better than assuming no reset at all, otherwise it
  says nothing instead of guessing. This is inferred, not confirmed. The page says so, and you
  should still check the real reset schedule with your cluster admins if it matters.
- Job cost table: `scontrol show partition` for `TRESBillingWeights`, applied to the reference
  job size from setup, using Slurm's own max() billing rule.

## Customizing

- Colors: set `ACCENT` in `config.env`. The rest of the per-person palette is generated from it
  automatically. Each person still keeps the same color from day to day
  (`user_colors.json`, regenerated each run, don't hand-edit it).
- Reference job size, refresh time, account, partition names shown: all in `config.env`, re-run
  `./setup.sh` or edit the file and rebuild.
- Wording: `bin/build_usage_page.py` is one plain Python file generating HTML strings, no
  templating engine, so any sentence on the page is a string you can search for and change.

## Files

```
config.example.env             every setting, documented (copy to config.env)
setup.sh                       interactive setup wizard
bin/build_usage_page.py        builds index.html / index_artifact.html / bars_only.html
bin/deploy_cloudflare.sh       uploads index.html to Cloudflare Pages (skips if unset)
bin/post_slack.py              posts bars_only.html as a PNG to Slack (skips if unset)
bin/refresh_daily.sbatch.template   filled in by setup.sh, becomes refresh_daily.sbatch
math/                          KaTeX (render_cli.js + its CSS). npm install here for KaTeX
docs/SLACK_SETUP.md            step by step Slack app setup
docs/CLOUDFLARE_SETUP.md       step by step Cloudflare Pages setup
```

After setup, your install also has (all git-ignored, all local/generated): `config.env`,
`refresh_daily.sbatch`, `index.html`, `index_artifact.html`, `bars_only.html`, `bars.png`,
`user_colors.json`, `slack_last_file.json`, `logs/`.

## License

MIT, see `LICENSE`.
