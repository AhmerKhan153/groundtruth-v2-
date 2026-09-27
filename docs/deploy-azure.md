# Deploying Groundtruth to Azure, with CI/CD (web UI)

Every step here is done in a web page: GitHub, MongoDB Atlas, the Azure portal and
Telegram. Do the steps **in order**; a step only ever uses things made in earlier
steps, and says which ("Needs"). Each step ends with a **Check** — don't move on
until it passes.

> Portal button names drift a little over time. If a label differs slightly, pick
> the closest match. (`deploy/deploy.sh` does most of Parts D and E in one command,
> if you ever want the CLI route instead.)

## What you'll end up with

```
push to main ──► GitHub Actions: test ─► build image ─► deploy (swap image)
                                            │                   │
                                 GitHub registry (private)   Azure Container Apps
                                                             ├─ app  "groundtruth"          ◄── Telegram button taps
                                                             └─ job  "groundtruth-source"   ── daily 05:00 UTC = 09:00 Dubai
                                                                        │
                                                               MongoDB Atlas (free) · DeepSeek
```

## Values sheet

Keep a private note open and fill it in as you go. Later steps paste from it.
**Never commit these.**

| # | Value | Comes from |
|---|---|---|
| V1 | Image tag (a 40-character commit SHA) | Step 3 |
| V2 | GitHub token (`ghp_…`, read-only packages) | Step 4 |
| V3 | Atlas connection string (`mongodb+srv://…`) | Step 9 |
| V4 | DeepSeek API key | Step 10 |
| V5 | Telegram bot token | Step 10 |
| V6 | Telegram chat id | Step 10 |
| V7 | Webhook secret (random) | Step 11 |
| V8 | Job secret (random) | Step 11 |
| V9 | App URL (`https://groundtruth.….azurecontainerapps.io`) | Step 16 |
| V10 | Application (client) ID | Step 27 |
| V11 | Directory (tenant) ID | Step 27 |
| V12 | Subscription ID | Step 28 |

---

# Part A — GitHub: build the first image

### Step 1. Put the code on `main`
This is the only part done in a terminal, because it's how code reaches GitHub
(GitHub Desktop works too):

```bash
git checkout main
git merge cloud-deployment
git push origin main
```

**Check:** on github.com, your repo's `main` branch shows `Dockerfile`, `deploy/` and
`.github/workflows/ci-cd.yml`.

### Step 2. Watch the first pipeline run
*Needs: step 1.*

1. Repo → **Actions** tab → workflow **ci-cd** → the run for your push.
2. Wait for it to finish.

**Check:** `test` ✅, `build` ✅, `deploy` shows **skipped**. Skipped is correct: Azure
sign-in isn't set up until Part F.

### Step 3. Copy the image tag
*Needs: step 2.*

1. Click your **profile picture** (top right) → **Your profile** → **Packages** tab.
2. Open **groundtruth**. It's marked **Private**.
3. Under recent tagged versions, copy the tag: a 40-character SHA. It equals the
   commit of step 1. → **V1**

**Check:** the image name reads `ghcr.io/ahmerkhan153/groundtruth:<V1>`.

### Step 4. Create a read-only token for Azure
Azure needs this to download your private image. Azure downloads it every time the
app wakes from zero, so when this token expires the bot stops at its next wake-up.

1. **Profile picture** → **Settings** → **Developer settings** (bottom of the left
   menu) → **Personal access tokens** → **Tokens (classic)**.
2. **Generate new token** → **Generate new token (classic)**.
3. **Note:** `azure-pull-groundtruth`. **Expiration:** the longest you're comfortable
   with; put a reminder in your calendar a week before it expires.
4. **Scopes:** tick **only** `read:packages`.
5. **Generate token** → copy it now (it's shown once). → **V2**

**Check:** the token list shows `azure-pull-groundtruth` with the scope `read:packages`.

---

# Part B — MongoDB Atlas: the database

### Step 5. Create the free cluster
1. Sign in at cloud.mongodb.com. Create an organization and project if asked
   (any names, e.g. `groundtruth`).
2. **Create** (or **Build a Database**) → choose **M0 / Free**.
3. **Provider:** Azure. **Region:** `UAE North` if offered for M0; otherwise the
   closest region that offers M0. Remember the region: step 12 uses the same one.
4. **Name:** `groundtruth` → **Create Deployment**.
5. If a "Connect" pop-up appears with a suggested user, close it; step 6 creates the user.

**Check:** the cluster shows as active (takes a few minutes).

### Step 6. Create the database user
*Needs: step 5.*

1. Left menu → **Security** → **Database Access** → **Add New Database User**.
2. **Authentication:** Password. **Username:** `groundtruth-app`. **Password:**
   **Autogenerate Secure Password**, then **Copy** it into your note (used in step 9).
3. **Database User Privileges** → **Specific Privileges** → **Add Specific Privilege**
   → role **readWrite**, database **`groundtruth`**, collection left empty.
   *Why:* if this password ever leaks, it can only touch this one database.
4. **Add User**.

**Check:** the user is listed with `readWrite@groundtruth`.

### Step 7. Allow the app to connect
*Needs: step 5.*

1. Left menu → **Security** → **Network Access** → **Add IP Address**.
2. **Allow Access from Anywhere** (fills in `0.0.0.0/0`) → **Confirm**.
   *Why:* Azure Container Apps has no fixed outgoing IP unless you pay for extra
   networking. The scoped user and strong password from step 6 are the protection.

**Check:** `0.0.0.0/0` shows as **Active**.

### Step 8. Create the collection and its indexes
*Needs: step 5.* These indexes make duplicate stories impossible and make old
records delete themselves.

1. Left menu → **Database** → your cluster → **Browse Collections** → **Create Database**.
2. **Database name:** `groundtruth`. **Collection name:** `drafts` → **Create**.
3. Open `drafts` → **Indexes** tab → **Create Index** three times, one per row:

   | Fields | Options |
   |---|---|
   | `{ "hn_id": 1 }` | `{ "unique": true }` |
   | `{ "expire_at": 1 }` | `{ "expireAfterSeconds": 0 }` |
   | `{ "status": 1, "announced": 1 }` | *(leave empty)* |

**Check:** the Indexes tab lists `_id_` plus your three, with `hn_id` marked
**UNIQUE** and `expire_at` marked **TTL**.

### Step 9. Get the connection string
*Needs: steps 5 and 6.*

1. **Database** → your cluster → **Connect** → **Drivers**.
2. Copy the string shown (`mongodb+srv://groundtruth-app:<db_password>@…`).
3. Replace `<db_password>` with the password from step 6. → **V3**

**Check:** V3 starts with `mongodb+srv://groundtruth-app:` and contains no `<` or `>`.

---

# Part C — Collect the remaining values

### Step 10. Copy existing values
The same bot serves your laptop and the cloud. From your local `.env`:
- `LLM_API_KEY` → **V4**
- `TELEGRAM_BOT_TOKEN` → **V5**
- `TELEGRAM_CHAT_ID` → **V6**

**Check:** V4, V5 and V6 are filled in.

### Step 11. Make two random secrets
Use your password manager's generator: **40 characters, letters and digits only**
(no symbols; Telegram only allows letters, digits, `_` and `-` in its secret).
- One → **V7** (Telegram webhook secret)
- Another → **V8** (job secret, for manual triggers)

Also add `TELEGRAM_WEBHOOK_SECRET=<V7>` to your local `.env`. When you test locally,
`dev_poll.py` pauses the cloud webhook and needs V7 to restore it afterwards.

**Check:** V7 and V8 are different, 40 characters, letters and digits only.

---

# Part D — Azure: the app

### Step 12. Create the resource group
A resource group is the folder holding everything for this project; deleting it
deletes it all.

1. portal.azure.com → search **Resource groups** → **Create**.
2. **Subscription:** yours. **Name:** `groundtruth-rg`. **Region:** the region from step 5.
3. **Review + create** → **Create**.

**Check:** `groundtruth-rg` appears in the Resource groups list.

### Step 13. Create the Container App (and its environment)
*Needs: steps 3, 4 and 12.*

Search **Container Apps** → **Create** → **Container App**.

**Basics tab**
- **Subscription:** yours. **Resource group:** `groundtruth-rg`.
- **Container app name:** `groundtruth`.
- **Deployment source:** Container image.
- **Region:** the region from step 5. If it isn't in the list, pick the closest one
  offered; it still works, just a little further from the database.
- **Container Apps environment:** **Create new** →
  - **Environment name:** `groundtruth-env`.
  - **Plan / Environment type:** **Consumption only** (or Workload profiles, using only
    the Consumption profile). This is the plan with the free monthly grant.
  - **Monitoring** tab → **Logs destination:** Azure Log Analytics → **Create new**
    workspace named `groundtruth-logs`.
  - **Create** (closes the pop-up).

**Container tab**
- Untick **Use quickstart image**.
- **Name:** `groundtruth`.
- **Image source:** Docker Hub or other registries. **Image type:** **Private**.
- **Registry login server:** `ghcr.io`
- **Registry user name:** your GitHub username (`AhmerKhan153`)
- **Registry password:** **V2**
- **Image and tag:** `ahmerkhan153/groundtruth:<V1>`
- **Command override / Arguments override:** leave empty.
- **CPU and Memory:** **0.25 CPU cores, 0.5 Gi memory**.
- **Environment variables:** add these nine, all **Source: Manual entry**. The
  sensitive ones come in step 15, as secrets.

  | Name | Value |
  |---|---|
  | `LLM_API_BASE` | `https://api.deepseek.com` |
  | `LLM_MODEL` | `deepseek-flash` |
  | `LLM_EXTRA_BODY` | `{"thinking": {"type": "disabled"}}` |
  | `LLM_MAX_TOKENS` | `4096` |
  | `LLM_TIMEOUT_SECONDS` | `45` |
  | `LLM_MAX_RETRIES` | `1` |
  | `MONGODB_DB_NAME` | `groundtruth` |
  | `TELEGRAM_CHAT_ID` | **V6** |
  | `STORIES_PER_RUN` | `6` |

**Ingress tab**
- **Ingress:** Enabled. **Ingress traffic:** Accepting traffic from anywhere.
- **Ingress type:** HTTP. **Target port:** `8080`.

**Review + create** → **Create**. Wait for "Your deployment is complete" → **Go to resource**.

**Check:** the app's **Overview** shows **Running status: Running** (or "Scaled to 0").

### Step 14. Scale to zero, at most one copy
*Needs: step 13.* New apps may default to keeping a copy always running, which is billed.

1. App → left menu **Application** → **Scale** (or **Scale and replicas**) →
   **Edit and deploy** if asked.
2. **Min replicas:** `0`. **Max replicas:** `1`.
3. Keep the default HTTP scale rule. **Save** / **Create** (makes a new revision).

**Check:** Scale shows min 0, max 1.

### Step 15. Add the secrets
*Needs: steps 9, 10, 11 and 13.* Secrets are stored encrypted and hidden in the
portal, unlike plain environment variables.

App → **Settings** → **Secrets** → **Add**, five times (type: **Container Apps Secret**):

| Key | Value |
|---|---|
| `llm-api-key` | **V4** |
| `mongodb-uri` | **V3** |
| `telegram-bot-token` | **V5** |
| `telegram-webhook-secret` | **V7** |
| `job-secret` | **V8** |

(A `ghcr-io…` registry-password secret already exists from step 13; leave it.)

**Check:** Secrets lists your five plus the registry one.

### Step 16. Connect the secrets to the app
*Needs: step 15.* Secrets do nothing until an environment variable points at them.

1. App → **Application** → **Revisions and replicas** (or **Containers**) →
   **Edit and deploy**.
2. Select the `groundtruth` container → **Edit** → **Environment variables** tab.
3. **Add** five, each **Source: Reference a secret**:

   | Name | Secret |
   |---|---|
   | `LLM_API_KEY` | `llm-api-key` |
   | `MONGODB_URI` | `mongodb-uri` |
   | `TELEGRAM_BOT_TOKEN` | `telegram-bot-token` |
   | `TELEGRAM_WEBHOOK_SECRET` | `telegram-webhook-secret` |
   | `JOB_SECRET` | `job-secret` |

4. **Save** → **Create** (new revision).
5. App → **Overview** → copy **Application Url**. → **V9**

**Check:** open `<V9>/healthz` in your browser. The first load can take ~10–20 s
(waking from zero) and then shows `{"ok":true}`.

### Step 17. Cap log costs
*Needs: step 13 (it created the workspace).*

1. Search **Log Analytics workspaces** → `groundtruth-logs`.
2. **Usage and estimated costs** → **Daily cap** → **On**, **0.1 GB/day** → **OK**.

**Check:** Daily cap shows 0.1 GB.

---

# Part E — Azure: the daily job, and Telegram

### Step 18. Create the scheduled job
*Needs: steps 3, 4, 12 and 13 (it reuses the environment).*

Search **Container App Jobs** → **Create**.

**Basics tab**
- **Resource group:** `groundtruth-rg`. **Job name:** `groundtruth-source`.
- **Region:** same as the app. **Container Apps environment:** `groundtruth-env`.
- **Trigger type:** **Schedule**. **Cron expression:** `0 5 * * *`.
  Azure evaluates this in **UTC**: 05:00 UTC = 09:00 Dubai. It runs daily; the app
  skips itself unless 44 h have passed, so a pick list arrives every other day.
- **Replica timeout:** `300` seconds. **Retry limit:** `1`.
  **Parallelism** and **Replica completion count:** `1`.

**Container tab**
- Untick **Use quickstart image**. **Name:** `source`.
- **Image source:** Docker Hub or other registries, **Private**, login server `ghcr.io`,
  user name `AhmerKhan153`, password **V2**, image `ahmerkhan153/groundtruth:<V1>`.
- **Command override:** `python, -m, scripts.run_source` (comma-separated, one part
  per item; follow the field's own hint if it shows a different format).
- **CPU and Memory:** 0.25 CPU, 0.5 Gi.
- **Environment variables** (Manual entry):

  | Name | Value |
  |---|---|
  | `MONGODB_DB_NAME` | `groundtruth` |
  | `TELEGRAM_CHAT_ID` | **V6** |
  | `STORIES_PER_RUN` | `6` |

**Review + create** → **Create** → **Go to resource**.

**Check:** the job's Overview shows trigger type Schedule, `0 5 * * *`.

### Step 19. Give the job its two secrets
*Needs: step 18.* The job only sources stories and messages you, so it needs just
the database and the bot. It never calls DeepSeek.

1. Job → **Settings** → **Secrets** → **Add**:

   | Key | Value |
   |---|---|
   | `mongodb-uri` | **V3** |
   | `telegram-bot-token` | **V5** |

2. Job → **Application** → **Containers** → select `source` → **Edit** →
   **Environment variables** → **Add** (Reference a secret):

   | Name | Secret |
   |---|---|
   | `MONGODB_URI` | `mongodb-uri` |
   | `TELEGRAM_BOT_TOKEN` | `telegram-bot-token` |

3. **Save**.

**Check:** the container's environment variables list shows five entries.

### Step 20. Point Telegram at the app
*Needs: steps 11 and 16.* This tells Telegram where to send button taps, and which
secret to send with them. Telegram has no settings page for this; you open one
link in your browser.

Build this link from V5, V9 and V7 (all on one line, no spaces):

```
https://api.telegram.org/bot<V5>/setWebhook?url=<V9>/telegram&secret_token=<V7>&allowed_updates=%5B%22callback_query%22%5D&drop_pending_updates=true
```

Open it. You should see `{"ok":true,"result":true,"description":"Webhook was set"}`.

**Check:** open `https://api.telegram.org/bot<V5>/getWebhookInfo`. It shows your
`<V9>/telegram` URL, `"pending_update_count":0` and no `last_error_message`.

### Step 21. First real run
*Needs: steps 16, 19 and 20.*

1. Job → **Overview** → **Run now**. It sends a pick list: this is the first run
   ever, so the 44 h check lets it through.
2. Job → **Execution history**: the run turns **Succeeded** within a minute or two.

**Check:** a numbered pick list arrives in Telegram.

### Step 22. Try the whole flow on your phone
*Needs: step 21.*

1. Tap a number → the message "Drafting: …" turns into a draft (up to ~30 s,
   longer if the app was asleep).
2. Tap **Rewrite** → the draft changes.
3. Tap **Approve** → the draft is marked ✅ and a second message arrives with just the
   post, ready to copy into LinkedIn.
4. Tap Approve again → a small toast says "Already approved." and nothing repeats.

**Check:** in Atlas → Browse Collections → `groundtruth.drafts`, that story's record
has `status: "approved"` and `expire_at: null`.

### Step 23. Confirm the every-other-day spacing
*Needs: step 21.*

1. Job → **Run now** again → Execution history → **Succeeded**.
2. Open that execution's **Console logs** (or App → Monitoring → Log stream).

**Check:** the log line reads `{"status": "skipped"}` and no new pick list arrives.

---

# Part F — CI/CD: GitHub deploys every push

This gives GitHub an Azure identity that works **only** from this repo's `main`
branch, with rights **only** on `groundtruth-rg`. No Azure password is stored in
GitHub.

### Step 24. Register an identity for GitHub
1. Portal → search **Microsoft Entra ID** → **App registrations** → **New registration**.
2. **Name:** `groundtruth-github`. **Supported account types:** this organizational
   directory only (single tenant). **Redirect URI:** leave empty.
3. **Register**.

**Check:** the app registration's Overview page opens.

### Step 25. Trust GitHub's `main` branch
*Needs: step 24.*

1. In `groundtruth-github` → **Certificates & secrets** → **Federated credentials**
   tab → **Add credential**.
2. **Federated credential scenario:** **GitHub Actions deploying Azure resources**.
3. **Organization:** `AhmerKhan153`. **Repository:** `groundtruth-v2-` (exactly as on
   GitHub, including the trailing dash). **Entity type:** **Branch**. **GitHub branch
   name:** `main`.
4. **Name:** `github-main` → **Add**.

**Check:** the credential's subject reads
`repo:AhmerKhan153/groundtruth-v2-:ref:refs/heads/main`.

### Step 26. Allow it to update the resource group, and nothing else
*Needs: steps 12 and 24.*

1. Portal → **Resource groups** → `groundtruth-rg` → **Access control (IAM)** →
   **Add** → **Add role assignment**.
2. **Role** tab → **Privileged administrator roles** → **Contributor** → **Next**.
3. **Members** tab → **Assign access to:** User, group, or service principal →
   **Select members** → search `groundtruth-github` → select → **Next**.
4. **Review + assign**. (If asked about conditions, accept the default.)

**Check:** `groundtruth-rg` → Access control (IAM) → **Role assignments** lists
`groundtruth-github` as **Contributor**.

### Step 27. Client and tenant IDs
*Needs: step 24.*

In `groundtruth-github` → **Overview**, copy:
- **Application (client) ID** → **V10**
- **Directory (tenant) ID** → **V11**

### Step 28. Subscription ID
Portal → search **Subscriptions** → your subscription → copy **Subscription ID** → **V12**.

### Step 29. Add the six variables to GitHub
*Needs: steps 27 and 28.*

Repo → **Settings** → **Secrets and variables** → **Actions** → **Variables** tab →
**New repository variable**, six times. The last three must match the names you
used in steps 12, 13 and 18 exactly.

| Name | Value |
|---|---|
| `AZURE_CLIENT_ID` | **V10** |
| `AZURE_TENANT_ID` | **V11** |
| `AZURE_SUBSCRIPTION_ID` | **V12** |
| `AZ_RESOURCE_GROUP` | `groundtruth-rg` |
| `AZ_APP` | `groundtruth` |
| `AZ_JOB` | `groundtruth-source` |

*Why variables and not secrets:* these are identifiers, not passwords. Access comes
from the trust in step 25, which only accepts GitHub's tokens for `main` of this repo.

**Check:** the Variables tab lists all six.

### Step 30. Run the pipeline end to end
*Needs: steps 25, 26 and 29.*

1. Repo → **Actions** → **ci-cd** → **Run workflow** → branch `main` → **Run workflow**.
2. Wait for it to finish.

**Check:** `test` ✅, `build` ✅, `deploy` ✅, with a notice reading
`Deployed ghcr.io/ahmerkhan153/groundtruth:… to https://…`. Then open `<V9>/healthz`:
still `{"ok":true}`. From now on every push to `main` deploys by itself.

---

# Part G — Guardrails

### Step 31. Budget alert
1. Portal → **Cost Management + Billing** → **Cost Management** → **Budgets** → **Add**.
2. **Scope:** your subscription. **Name:** `groundtruth`. **Reset period:** Monthly.
   **Amount:** e.g. `5` (in your billing currency, AED).
3. **Alert conditions:** Actual 50% and 100% → your email → **Create**.

**Check:** the budget is listed. (Expected spend is zero: everything fits the free grants.)

### Step 32. DeepSeek spending cap
Keep a prepaid DeepSeek balance rather than anything open-ended, so spending can
never exceed what you topped up.

**Check:** your DeepSeek dashboard shows a positive balance.

---

## Day to day

| I want to… | Do this |
|---|---|
| Ship a code change | Open a PR (CI runs the tests) → merge → CI deploys |
| Change a secret | App (and job, if it has that secret) → **Secrets** → edit → then **Revisions** → **Restart** the active revision |
| Change a setting | App → **Containers** → **Edit and deploy** → edit the variable → **Create** |
| Roll back | Repo → **Actions** → an older successful **ci-cd** run → **Re-run jobs** → the deploy job redeploys that image |
| Send a pick list now | Job → **Run now** (respects the 44 h spacing) |
| See what happened | App or Job → **Monitoring** → **Log stream** / Execution history |
| Test on your laptop | `python -m scripts.dev_poll --source` (pauses the cloud webhook, restores it on Ctrl+C) |
| Renew the GitHub token | New classic token (step 4) → App **and** Job → **Registries** (or Secrets → the `ghcr-io…` one) → update the password |
| Stop the bot | Open `https://api.telegram.org/bot<V5>/deleteWebhook`, and in the Job → **Disable** the schedule |

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `deploy` job **skipped** | A variable from step 29 is missing, or the run isn't on `main` |
| `deploy` fails with **AADSTS70021** / "no matching federated identity" | Step 25's organization, repository or branch differs from GitHub's, e.g. the trailing dash or letter case |
| `deploy` fails with **AuthorizationFailed** | Step 26's role is missing, or was given on another resource group |
| App stuck starting; logs mention **unauthorized / pull** | GitHub token expired or lacks `read:packages` → see "Renew the GitHub token" |
| Buttons show a spinner, then nothing | `getWebhookInfo`: a 401 in `last_error_message` means V7 in Telegram and in the app differ → redo step 20 with the app's value |
| "This story has expired." on every tap | You tapped a message from the other environment (laptop vs cloud database) |
| Job **Succeeded** but no pick list | Normal on skip days (44 h spacing); its log says `"skipped"` |
| App errors mention **timed out** reaching Mongo | Atlas Network Access is missing `0.0.0.0/0` (step 7), or V3 has the wrong password |
