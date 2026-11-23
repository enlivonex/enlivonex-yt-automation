# Enlivonex beta website

The site and its API run from one Cloudflare Worker. Cloudflare D1 stores beta
requests; the Windows ZIP must be hosted separately at a public HTTPS URL (for
example, as a GitHub Release asset). R2 is not used.

## What the beta flow does

1. A visitor signs in through Google Identity Services. The Worker verifies the
   Google ID token with Google's token-info endpoint and accepts only a
   verified `@gmail.com` address.
2. The visitor submits their PC type and consent. The Worker stores the Gmail
   address, PC type, request status, and timestamps in D1.
3. The publisher reviews requests in `/admin.html`, contacts selected testers
   manually, and sends payment instructions/QR outside the website.
4. After independently confirming the ₹100 payment, the publisher issues a
   one-time code in the admin page and emails it to the verified Gmail address.
5. The tester signs in with the same Gmail account and redeems the code. The
   Worker consumes the code once and returns the configured public ZIP URL.

The code is a light gate, not file protection: anyone who obtains the public
ZIP URL can use or share it without signing in or paying. Do not treat the
public URL as a secret. The code does not activate the installed app or provide
per-device licensing.

Google sign-in verifies Gmail ownership for the request; it does not connect a
tester to YouTube or grant YouTube permissions. If the desktop app's Google
OAuth consent screen is in testing mode, the publisher must separately add
selected tester addresses to the Google Cloud test-user list.

## Data and email

The site stores the verified Gmail address, selected PC type, request status,
payment/code timestamps, and redemption status until an administrator deletes
the request. The Google ID token is sent to Google's token-info endpoint for
verification and is not stored by the Worker. The site does not collect Google
passwords. The publisher manually checks requests and uses the company
mailbox to send payment details and codes; the Worker is not connected to an
email provider.

The public form is for people with a verified Gmail account. Disposable email
addresses are rejected because users must complete Google sign-in; checking
the spelling of an `@gmail.com` address alone would not prove ownership.

## Configure the site

### 1. Prepare the public ZIP URL

Upload `dist\Enlivonex-Windows.zip` to a public release host such as GitHub
Releases. The resulting URL must be a direct HTTPS asset URL that works without
sign-in. For GitHub it will look like:

```text
https://github.com/OWNER/REPOSITORY/releases/download/TAG/Enlivonex-Windows.zip
```

The download is intentionally public after code redemption. Anyone can share
the URL, and a user can download directly without visiting this site once they
know it. Cloudflare Worker Static Assets cannot host this approximately 109 MB
ZIP as a site asset; keep it on the external release host.

Before publishing, inspect the archive and ensure it contains no user tokens,
`.env`, AI API keys, or private app data. The Google Desktop OAuth client
configuration may be present in the desktop app package, but desktop OAuth
client secrets are extractable and must not be used as server credentials.

### 2. Create the website's Google OAuth client

In the Enlivonex Google Cloud project:

1. Configure the Google Auth Platform consent/branding details as required.
2. Create an OAuth client with application type **Web application**. Do not
   reuse the desktop OAuth client JSON bundled with the Windows app.
3. Add the exact deployed `workers.dev` origin under **Authorized JavaScript
   origins** (for example, `https://YOUR-SUBDOMAIN.workers.dev`).
4. Copy the web client ID into `GOOGLE_CLIENT_ID` in
   `marketing\download-worker\wrangler.toml`.
5. Add the same workers.dev hostname as an allowed test domain/audience setting
   if Google Cloud requests it. Follow the current Google Cloud console
   validation prompts.

The web client ID is public configuration, not a secret. The Worker validates
the token's audience, issuer, expiration, verified-email claim, and Gmail
domain. The desktop app's separate YouTube OAuth consent/testing requirements
still apply.

### 3. Configure Cloudflare

The D1 database `enlivonex-access` has already been created for this account.
Its ID is in `marketing\download-worker\wrangler.toml`. The R2 subscription and
bucket are not required for this public-link flow.

From `marketing\download-worker`:

```powershell
npm ci
npx wrangler d1 execute enlivonex-access --remote --file .\schema.sql
```

Set `GOOGLE_CLIENT_ID` and `PUBLIC_DOWNLOAD_URL` in the `[vars]` section of
`wrangler.toml`. Use the Web OAuth client ID and the direct public ZIP URL from
the previous steps. Generate and store an admin secret in a password manager,
then set it without putting it in source control or chat:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
npx wrangler secret put ADMIN_SECRET
```

Enter the generated value directly at Wrangler's prompt.

### 4. Test locally

For an interactive local preview, copy `.dev.vars.example` to `.dev.vars` and
fill in a local admin secret, the Web OAuth client ID, and a public HTTPS ZIP
URL. Add `http://localhost:8787` to that OAuth client's authorized JavaScript
origins. Then initialize local D1 and start Wrangler:

```powershell
npx wrangler d1 execute enlivonex-access --local --file .\schema.sql
npm run dev
```

`.dev.vars` is ignored by Git. Never use a production admin secret for local
development.

### 5. Test and deploy

Run the automated Worker tests and a deployment dry run:

```powershell
npm test
npx wrangler deploy --dry-run
```

When Google and the ZIP URL are configured, deploy from the same directory:

```powershell
npm run deploy
```

The site stays closed to requests until the Google client ID, valid public
HTTPS ZIP URL, D1 binding, and admin secret are configured. After deployment,
test Google sign-in, a request, admin review, code issuance, redemption with
the same Gmail account, and the public download link. Confirm that a different
Gmail account cannot redeem the code.

## Admin and operations

Open `<workers.dev URL>/admin.html`. The page is publicly reachable; the
`ADMIN_SECRET` protects its API. Enter the secret only on a trusted device and
clear it when finished. The page lists requests and allows manual payment
confirmation, code issuance/reissue, and record deletion. Generated codes are
shown once; copy and email them to the verified address manually.

Cloudflare Free plan limits and provider terms can change. D1 and Worker usage
have quotas, and the external release host has its own policies and limits.
The website's free hosting does not make the entire download flow immune to
quota changes or future charges.
