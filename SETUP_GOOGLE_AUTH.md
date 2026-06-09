# Setting up Google (Gmail) sign-in

Agent-Aware can require every visitor to sign in with their Google account
before using the app. This is handled by Streamlit's built-in OAuth — you just
need to create a Google OAuth client and paste two values into
`.streamlit/secrets.toml`.

Until you do this, the app runs in **open mode** (a small notice shows at the
top, and no login is required).

---

## Step 1 — Create a Google OAuth client

1. Go to the **Google Cloud Console**: https://console.cloud.google.com/
2. Create a project (or pick an existing one) — top-left project dropdown → **New Project**.
3. In the left menu: **APIs & Services → OAuth consent screen**
   - User type: **External** → Create
   - Fill in app name (e.g. "Agent-Aware"), your email, and a developer email. Save.
   - On the **Test users** step, add the Gmail addresses that should be allowed
     to sign in while the app is in "Testing" mode (including your own).
4. Left menu: **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Web application**
   - Name: anything (e.g. "Agent-Aware local")
   - **Authorized redirect URIs → Add URI**, paste **exactly**:
     ```
     http://localhost:8501/oauth2callback
     ```
   - Click **Create**.
5. A popup shows your **Client ID** and **Client Secret** — copy both.

---

## Step 2 — Paste the values into secrets

Open `.streamlit/secrets.toml` and replace the two placeholders:

```toml
client_id     = "PASTE_YOUR_GOOGLE_CLIENT_ID_HERE"      ← your Client ID
client_secret = "PASTE_YOUR_GOOGLE_CLIENT_SECRET_HERE"  ← your Client Secret
```

(The `cookie_secret` is already filled in for you. Leave `redirect_uri` and
`server_metadata_url` as-is.)

---

## Step 3 — Restart the app

```
python run.py
```

Now visiting http://localhost:8501 shows a **"Continue with Google"** screen.
After signing in, the user's name appears top-right with a **Sign out** option.

---

## Optional — restrict to specific people

By default, **any** Google account can sign in. To allow only certain Gmail
addresses, edit the bottom of `.streamlit/secrets.toml`:

```toml
[auth_allowlist]
emails = ["you@gmail.com", "friend@gmail.com"]
```

Anyone not on the list gets an "Access not enabled" screen after signing in.

---

## Going live later (not just localhost)

When you deploy to a real domain, add that domain's callback to the OAuth
client's Authorized redirect URIs (e.g. `https://yourapp.com/oauth2callback`)
and update `redirect_uri` in secrets to match.
