![hpc-usage-dashboard](docs/title.svg)

A simple, daily-updated HTML dashboard for a lab's shared compute allocation. It shows usage (by
each lab member) for each capped resource, recent job statistics, and (when relevant) what a job
costs on each partition. A daily `sbatch` job rebuilds it from Slurm's own accounting. Host it
wherever you like (e.g. a free Cloudflare Pages link), and optionally post the usage bars to your lab's Slack
every day at some user-set time.

The dashboard works on any cluster using Slurm to schedule jobs and track usage. It finds whatever
resources your account is actually capped on and builds one usage bar per capped resource.
Billing-hours and GPU-hours (Skipjack's setup) are this README's running examples, but the same
tool works for an account capped on anything else.

<img src="docs/screenshots/demo-bars.png" alt="Two usage bars, billing-hours and GPU-hours, broken down by person" width="70%">


From `demo/`, a fully fabricated example (fictional lab, cluster, and usage) you can run yourself
with no Slurm access: see [Try it without Slurm](#try-it-without-slurm).

## What's in the dashboard?

- How much of each capped resource is left, one bar per resource (billing-hours and GPU-hours in
  the example), broken down by person. Colors are generated from one accent color you pick.
- What ran since the last reset (or the past 30 days, if the cluster doesn't reset usage), by
  partition: jobs, hours charged against each capped resource, average job length.
- If the cluster uses a weighted billing formula: what a reference job costs and how long you could
  keep it running on each partition, cheapest to priciest GPU partition colored green to red.
- Dropdowns explaining the calculations and whether usage resets: typeset equations when Node.js
  is available, plain text otherwise, or a short code snippet when there's no formula to explain.

![The full dashboard: bars, recent-activity table, and job cost table, from the demo](docs/screenshots/demo-details.png)

## What info does this need to work?

- An account with at least one cap set in `GrpTRESMins` (`sacctmgr show assoc where
  account=<acct> format=GrpTRESMins` returns something).
- Read access to `sacctmgr`, `sshare`, `sacct` and `scontrol` for that account, the same info
  `sshare -A <acct>` already shows you.
- Python 3, and a login or submit node to run the daily job from.

At build time, the page reads the account's
`GrpTRESMins` and draws one bar per capped resource: `billing`, `gres/gpu` (or a specific GPU type
like `gres/gpu:a100`), `cpu`, `mem`, `node`, a `license/*`, etc.

The cost table only appears with a `billing` cap and `TRESBillingWeights` configured
(`scontrol show partition`).

_This dashboard only works for clusters whose accounting runs through Slurm's own TRES/billing system, not a separate ledger (e.g. "service units") tracked outside Slurm. Check with `sacctmgr` if unsure. An adaptor may be written for a separate ledger._

Optional:
- Node.js, for typeset equations (KaTeX) instead of plain LaTeX text.
- Firefox or Chrome/Chromium, headless, only needed for the Slack post (screenshots the bars).
- A free Cloudflare account, for hosting at a public but unlisted link. Skip this and use the local
  `index.html`, or host it some other way.
- A Slack app/bot, for the daily post into a channel.

## Setup

```
git clone https://github.com/sdharma5/hpc-usage-dashboard.git
cd hpc-usage-dashboard
./setup.sh
```

The wizard asks about your lab, cluster, account and colors, and optionally walks through
Cloudflare Pages and a Slack bot. It writes `config.env` (see `config.example.env` for what each
setting does; no secrets go in that file), generates `refresh_daily.sbatch`, and can build the page
and submit the daily job right away. Re-run it any time to change an answer.

Credentials never go in the repo. They're saved outside it, in
`~/.config/<APP_SLUG>/{cloudflare,slack}.env`, each chmod 600.

### Setting up by hand

```
cp config.example.env config.env    # edit it
mkdir -p logs
sed -e "s|@@APP_SLUG@@|myapp|g" -e "s|@@ACCOUNT@@|myacct|g" -e "s|@@PARTITION@@|general|g" \
    -e "s|@@ROOT@@|$PWD|g" -e "s|@@REFRESH_HOUR@@|06:00|g" -e "s|@@PYTHON@@|$(command -v python3)|g" \
    bin/refresh_daily.sbatch.template > refresh_daily.sbatch
python3 bin/build_usage_page.py     # builds index.html once, to check it
sbatch refresh_daily.sbatch         # runs now, then re-schedules itself daily
```

### Try it without Slurm

`demo/` is a fully fabricated example, no real data, no Slurm access needed:

```
cd demo
./run_demo.sh
```

Open the `index.html` it writes there. It works by putting stand-in
`sacctmgr`/`sshare`/`sacct`/`scontrol` scripts (`demo/fakebin/`) ahead of the real ones on `PATH`,
so the actual, unmodified builder runs against fabricated data. See `demo/README.md` for how it's
put together.

## How does the daily refresh work?

Most shared clusters don't allow a user's own cron or scrontab, so `refresh_daily.sbatch`
resubmits itself, chained day after day. Each run:

1. Queues tomorrow's run for the time you set in setup (done first so a failed run can't stall future ones).
2. Rebuilds the page: polls Slurm (`sacctmgr`, `sshare`, `sacct`, `scontrol`) for caps, usage,
   recent jobs, and partition weights, and regenerates `index.html` from scratch.
3. Uploads the new page to Cloudflare, if configured.
4. Posts the usage bars to Slack, if configured.


Check it's running: `squeue -u $USER -n <APP_SLUG>-daily`

Stop it: `scancel -u $USER -n <APP_SLUG>-daily` (cancels the waiting future run; nothing else to
clean up)

## How is usage computed?

- Caps: `sacctmgr show assoc ... format=GrpTRESMins`, converted from minutes to hours, one bar per
  capped resource found.
- Current usage: `sshare -A <acct> -a`, Slurm's own decayed usage total per user (the same
  numbers Slurm uses to throttle new jobs). `sshare` lists everyone who's ever touched the
  account, including people long gone, so anyone at zero across every capped resource who also
  hasn't run a job in the last 90 days is left off the page entirely, rather than piling up as a
  long list of "0 hrs" lines for departed members.
- Reset detection: your cluster may reset usage on a schedule (`PriorityUsageResetPeriod`). Slurm
  doesn't expose the last reset date, so the page infers it: it tests candidate start dates against
  job records, fading old jobs by the decay rate, and keeps whichever date best reproduces Slurm's
  current total. _Use an actual reset date from your cluster admins if you have one._
- Job cost table: only shown with a billing cap and `TRESBillingWeights` configured
  (`scontrol show partition`), applied to the reference job size from setup with Slurm's own max()
  rule. Otherwise the page shows the plain Slurm fields behind each cap instead.

## Customizing

- Colors: set `ACCENT` in `config.env`; the rest of the per-person palette is generated from it.
  Each person keeps the same color day to day (`user_colors.json`)
- Reference job size, refresh time, account, and which partition the daily job runs on: all in
  `config.env`; re-run `./setup.sh` or edit and rebuild. Which partitions *appear* isn't something
  you set: that's automatic, from whichever have `TRESBillingWeights`.
- Wording: `bin/build_usage_page.py` is one plain Python file generating HTML strings, no
  templating engine, so any sentence is a string you can search for and change.

## Files

```
config.example.env             every setting, documented (copy to config.env)
setup.sh                       interactive setup wizard
bin/build_usage_page.py        builds index.html / index_artifact.html / bars_only.html
bin/deploy_cloudflare.sh       uploads index.html to Cloudflare Pages (skips if unset)
bin/post_slack.py              posts bars_only.html as a PNG to Slack (skips if unset)
bin/refresh_daily.sbatch.template   filled in by setup.sh, becomes refresh_daily.sbatch
math/                          KaTeX (render_cli.js + its CSS); npm install here for KaTeX
docs/SLACK_SETUP.md            step-by-step Slack app setup
docs/CLOUDFLARE_SETUP.md       step-by-step Cloudflare Pages setup
docs/screenshots/              screenshots used above, from demo/
demo/                          fully fabricated example; see demo/README.md
```

After setup, your install also has (all git-ignored, all local/generated): `config.env`,
`refresh_daily.sbatch`, `index.html`, `index_artifact.html`, `bars_only.html`, `bars.png`,
`user_colors.json`, `slack_last_file.json`, `logs/`.

## License

MIT, see `LICENSE`.
