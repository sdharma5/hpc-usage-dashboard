# Setting up Cloudflare Pages hosting

Optional: hosts the page at a free, public-but-unlisted `https://<your-project>.pages.dev` link
that you can share (e.g. in Slack), without it being indexed by search engines (the page ships
`noindex`/`robots.txt`/a `_headers` rule already). `setup.sh` does the credential-saving step for
you — this page is the detail behind it.

## 1. Create a Cloudflare account

https://dash.cloudflare.com/sign-up — the free plan is enough for this.

## 2. Get your Account ID

Log into the dashboard; the **Account ID** is in the right-hand sidebar of most pages (or under
**Workers & Pages** in the sidebar). It's a 32-character hex string.

## 3. Create an API token

1. Click your profile icon (top right) → **My Profile** → **API Tokens**.
2. **Create Token**.
3. Use the **Edit Cloudflare Workers** template, or make a **Custom token** scoped to
   **Account → Cloudflare Pages → Edit** for your account. Either works; the custom one is
   narrower.
4. Create it and copy the token immediately — Cloudflare only shows it once. Treat it like a
   password: it's saved only in `~/.config/<APP_SLUG>/cloudflare.env`, mode 600, never in the
   repo or in chat with anyone.

## 4. Pick a project name

This becomes your URL: `https://<project-name>.pages.dev`. `setup.sh` suggests one with a random
suffix (e.g. `mylab-usage-9af82af2e5`) so it's hard to guess/find, since the page shows real
usernames. Use a plain, guessable name instead if that doesn't matter to you.

## 5. Save the credentials

`setup.sh` asks for the account ID and token and writes them to
`~/.config/<APP_SLUG>/cloudflare.env` (mode 600). To do it by hand instead:

```
mkdir -p ~/.config/<APP_SLUG>
umask 077
printf 'CLOUDFLARE_API_TOKEN=...\nCLOUDFLARE_ACCOUNT_ID=...\n' > ~/.config/<APP_SLUG>/cloudflare.env
chmod 600 ~/.config/<APP_SLUG>/cloudflare.env
```

Then set `CF_PROJECT=<project-name>` in `config.env`.

## Deploying

`bin/deploy_cloudflare.sh` runs `wrangler` (Cloudflare's CLI) if it's on `PATH`, otherwise falls
back to `npx --yes wrangler@3`, which needs internet access and Node.js/npm on the machine running
the daily job. The first run creates the Pages project automatically; later runs just upload the
new `index.html`.

## Troubleshooting

- **"CF_PROJECT is not set ... skipping"** — set it in `config.env`.
- **"upload skipped: ... not found"** — the secrets file above is missing.
- **"upload stopped: run chmod 600 ..."** — fix the file's permissions.
- **"UPLOAD FAILED (exit N)"** — the page on the cluster is still updated locally; only the
  online copy failed. Check the printed `wrangler` output above that line (the token is masked).
  Common causes: an expired/revoked token, wrong account ID, or no internet access from the node
  running the job (some clusters only allow outbound internet from login nodes, not compute
  nodes — run the daily job on a partition/node that has it).
