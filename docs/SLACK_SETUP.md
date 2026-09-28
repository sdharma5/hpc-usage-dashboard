# Setting up the Slack post

Optional: each morning's refresh can post a picture of the two usage bars into a Slack channel,
replacing the previous day's post. `setup.sh` will do the credential-saving step for you — this
page is the detail behind it, and what to do if something goes wrong.

## 1. Create the Slack app

1. Go to https://api.slack.com/apps and click **Create New App**.
2. Choose **From scratch**, give it a name (e.g. "usage"), and pick your workspace.
   Some workspaces require an admin to approve installing a new app — ask if it doesn't let you.

## 2. Give it permission to post and upload files

1. In the app's settings, open **OAuth & Permissions**.
2. Under **Bot Token Scopes**, add:
   - `chat:write` — post messages
   - `files:write` — upload the PNG
3. Click **Install to Workspace** (top of the OAuth & Permissions page) and approve it.
4. Copy the **Bot User OAuth Token** — it starts with `xoxb-`. This is what `setup.sh` (or
   `bin/post_slack.py`'s config) asks you for. Treat it like a password: it's saved only in
   `~/.config/<APP_SLUG>/slack.env`, mode 600, never in the repo or in chat with anyone.

## 3. Invite the bot to the channel

In the Slack channel you want the posts in, type:
```
/invite @<your app's name>
```
The bot cannot post to a channel it hasn't been invited to.

## 4. Get the channel ID

Right-click the channel name (or open its details panel) and choose **View channel details**.
The ID is at the bottom, starting with `C` (e.g. `C0123ABCDEF`) — that's what's asked for, not
the channel's `#name`.

## 5. Save the credentials

`setup.sh` asks for the token and channel ID and writes them to
`~/.config/<APP_SLUG>/slack.env` (mode 600). To do it by hand instead:

```
mkdir -p ~/.config/<APP_SLUG>
umask 077
printf 'SLACK_BOT_TOKEN=xoxb-...\nSLACK_CHANNEL_ID=C0123ABCDEF\n' > ~/.config/<APP_SLUG>/slack.env
chmod 600 ~/.config/<APP_SLUG>/slack.env
```

Then set `SLACK_ENABLED=true` in `config.env`.

## Troubleshooting

- **"slack: disabled in config.env, skipping"** — set `SLACK_ENABLED=true`.
- **"slack: no config at ... yet, skipping"** — the secrets file above is missing.
- **"slack: config must be chmod 600, skipping"** — run `chmod 600` on it.
- **"slack: ... failed: missing_scope"** — add the missing scope under Bot Token Scopes, then
  click **Reinstall to Workspace** on the same page (this may issue a new token — save it again).
- **"slack: ... failed: not_in_channel"** — invite the bot to the channel (step 3).
- **A stray "This file was deleted" message appears** — this happened on an older version of the
  script that could delete a file without deleting its message; the current script deletes the
  whole message first. If you see this, delete that one message by hand (or ask a workspace
  admin to, if you can't delete another user's/bot's message yourself).
- **No screenshot posted, page still updates fine** — a headless Firefox or Chrome/Chromium
  wasn't found on the machine running the daily job. Install one, or leave Slack posting off.
