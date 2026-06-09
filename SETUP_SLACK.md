# Connect Slack to Agent-Aware (read-only channel viewer)

This adds a **💬 Slack channels** panel to the top of the app where you can browse
your workspace's channels and read recent messages — directly in the website.

It's **read-only**: the app lists channels, reads recent messages, and shows who
posted them. It never posts, edits, or deletes anything.

> **What you must do yourself:** creating a Slack app and installing it to your
> workspace are account/consent actions — only you can do them. It takes ~5
> minutes. Once you paste the token into `.env`, the in-app feature is already
> built and ready.

---

## Step 1 — Create a Slack app

1. Go to **https://api.slack.com/apps** and sign in.
2. Click **Create New App** → **From scratch**.
3. App Name: `Agent-Aware`. Pick the **workspace** you want to browse. → **Create App**.

## Step 2 — Add read-only permissions (Bot Token Scopes)

1. In the left menu, open **OAuth & Permissions**.
2. Scroll to **Scopes → Bot Token Scopes** → **Add an OAuth Scope** and add these
   five (all read-only):

   | Scope | Lets the app… |
   |---|---|
   | `channels:read` | list public channels |
   | `groups:read` | list private channels it's in |
   | `channels:history` | read messages in public channels |
   | `groups:history` | read messages in private channels |
   | `users:read` | show who posted each message (display names) |

## Step 3 — Install to your workspace

1. Scroll up on the same page → **Install to Workspace** → **Allow**.
2. Copy the **Bot User OAuth Token** — it starts with **`xoxb-`**.

## Step 4 — Add the token to the app

Open `.env` in the project and paste it:

```
SLACK_BOT_TOKEN=xoxb-your-token-here
```

Then **restart the app** (the token is read at startup):

```
# stop the running app, then:
python run.py
```

Open **http://localhost:8501** → expand **💬 Slack channels** at the top. You'll
see your channels listed.

## Step 5 — Let the bot read a channel's messages

Listing channels works immediately. To **read messages** in a channel, the bot
must be a member of it (Slack's rule, not ours). In Slack:

- Open the channel → type **`/invite @Agent-Aware`** and send it.

Now select that channel in the app and its recent messages appear. If you didn't
invite the bot, the app tells you exactly which channel to invite it to.

---

### Notes
- **Private channels** only show up after you invite the bot to them.
- Nothing is stored — messages are fetched live (and cached for ~20s) each view.
- To turn the feature off, blank out `SLACK_BOT_TOKEN` and restart; the panel
  disappears.
- For deployment (Render/Docker), set `SLACK_BOT_TOKEN` as an environment
  variable in the hosting dashboard instead of `.env`.
