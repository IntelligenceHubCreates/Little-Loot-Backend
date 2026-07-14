# Little Loot — Manual Production Steps

This document lists every step that requires human action (AWS console, GitHub,
external provider dashboards, etc.) before the application is production-ready.
Complete these in order; later phases assume earlier ones are done.

---

## IMMEDIATE — Rotate Leaked Secrets

> The file `final-project/.env` was committed to git and contains live secrets.
> Even after removing it from git, the secrets are in git history and must be
> rotated NOW before any public deployment.

### 1. Google OAuth credentials
1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
2. Find the OAuth 2.0 Client ID used by this project
3. Click **Regenerate secret** (or delete and create a new one)
4. Update the new `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in AWS Secrets Manager (Phase 3)

### 2. Razorpay webhook secret
1. Go to [Razorpay Dashboard → Settings → Webhooks](https://dashboard.razorpay.com/app/webhooks)
2. Delete the existing webhook endpoint and re-create it with a new secret
3. Store the new secret as `RAZORPAY_WEBHOOK_SECRET` in AWS Secrets Manager

### 3. Razorpay API keys
1. Go to [Razorpay Dashboard → Settings → API Keys](https://dashboard.razorpay.com/app/keys)
2. Regenerate Test Mode keys (or Live Mode when you switch)
3. Store `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` in AWS Secrets Manager

### 4. NEXTAUTH_SECRET (frontend)
Generate a new one:
```
openssl rand -base64 32
```
Store it in Vercel Environment Variables as `NEXTAUTH_SECRET`.

### 5. Remove .env from git history
After rotating all secrets:
```bash
# In the final-project directory
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env" \
  --prune-empty --tag-name-filter cat -- --all
git push origin --force --all
```
Then add `final-project/.env` to `.gitignore` if not already there.

---

## Phase 2 — AWS Account Setup

### IAM credentials for Claude Code / Terraform
1. Log in to [AWS Console → IAM → Users](https://console.aws.amazon.com/iam/home#/users)
2. Create a user named `littleloot-deployer` with **programmatic access**
3. Attach the policy `AdministratorAccess` (narrow it down after first deploy)
4. Download the **Access key ID** and **Secret access key**
5. Configure locally:
   ```
   aws configure --profile littleloot
   # Enter the key ID, secret, region (ap-south-1), output (json)
   ```
6. Set in CI (GitHub Actions → Settings → Secrets):
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_REGION` = `ap-south-1`

---

## Phase 3 — AWS Secrets Manager

Create **one secret** named `littleloot/production` with these keys:

| Key | Value |
|-----|-------|
| `POSTGRES_USER` | (choose, e.g. `littlelootadmin`) |
| `POSTGRES_PASSWORD` | (generate: `openssl rand -base64 24`) |
| `POSTGRES_DB` | `littleloot` |
| `SECRET_KEY` | (generate: `openssl rand -base64 32`) — FastAPI JWT signing key |
| `RAZORPAY_KEY_ID` | From Razorpay dashboard |
| `RAZORPAY_KEY_SECRET` | From Razorpay dashboard |
| `RAZORPAY_WEBHOOK_SECRET` | From Razorpay dashboard |
| `CLOUDINARY_CLOUD_NAME` | From Cloudinary dashboard |
| `CLOUDINARY_API_KEY` | From Cloudinary dashboard |
| `CLOUDINARY_API_SECRET` | From Cloudinary dashboard |
| `RESEND_API_KEY` | From Resend dashboard |
| `INITIAL_ADMIN_EMAIL` | Admin email for first login |
| `INITIAL_ADMIN_PASSWORD_HASH` | bcrypt hash (see below) |

To generate `INITIAL_ADMIN_PASSWORD_HASH`:
```bash
python -c "from passlib.context import CryptContext; \
           print(CryptContext(schemes=['bcrypt']).hash('your-strong-password'))"
```

---

## Phase 4 — ECR Repository

Before running `terraform apply` for the backend, create the ECR repo manually
(Terraform references it):
1. [AWS Console → ECR → Create repository](https://console.aws.amazon.com/ecr/repositories)
2. Name: `littleloot-backend`
3. Region: `ap-south-1`
4. Note the **URI** (e.g. `123456789.dkr.ecr.ap-south-1.amazonaws.com/littleloot-backend`)

---

## Phase 5 — GitHub Repository for Backend

The backend remote needs to be changed from `DEMONDevelop/Silvee-Backend` to
`IntelligenceHubCreates/littleloot-backend`:

1. Create a new private repo `littleloot-backend` under `IntelligenceHubCreates`
   at [github.com/new](https://github.com/new)
2. In `Backend/Silvee-Backend/`:
   ```bash
   git remote set-url origin https://github.com/IntelligenceHubCreates/littleloot-backend.git
   git push -u origin main
   ```

---

## Phase 6 — RDS PostgreSQL

When Terraform creates the RDS instance it will need:
- Master username: value from `POSTGRES_USER` secret
- Master password: value from `POSTGRES_PASSWORD` secret
- Initial DB name: `littleloot`

After RDS is created, note the **Endpoint hostname** — it goes into the
`DATABASE_URL` environment variable for App Runner:
```
postgresql://<user>:<password>@<rds-endpoint>:5432/littleloot
```

---

## Phase 7 — DNS / Domain

Domain: `littlelootgifts.com`

After Vercel deploy:
1. In Vercel Dashboard → Project → Settings → Domains → Add `littlelootgifts.com`
2. In your DNS registrar, add the CNAME/A record Vercel shows you
3. Vercel auto-provisions TLS via Let's Encrypt

After App Runner deploy:
1. In [AWS App Runner → Custom domains](https://console.aws.amazon.com/apprunner/home)
2. Add `api.littlelootgifts.com`
3. Add the validation CNAME records to your DNS registrar
4. Update `ALLOWED_ORIGINS` env var in App Runner to include `https://littlelootgifts.com`
5. Update `NEXT_PUBLIC_BACKEND_URL` in Vercel to `https://api.littlelootgifts.com`

---

## Phase 8 — Vercel Environment Variables

In [Vercel Dashboard → Project → Settings → Environment Variables](https://vercel.com/dashboard),
add these for **Production** environment:

| Variable | Value |
|----------|-------|
| `NEXTAUTH_SECRET` | (rotated value from step 4 above) |
| `NEXTAUTH_URL` | `https://littlelootgifts.com` |
| `GOOGLE_CLIENT_ID` | (rotated) |
| `GOOGLE_CLIENT_SECRET` | (rotated) |
| `NEXT_PUBLIC_BACKEND_URL` | `https://api.littlelootgifts.com` |
| `BACKEND_URL` | `https://api.littlelootgifts.com` (server-side only) |
| `NEXT_PUBLIC_RAZORPAY_KEY_ID` | (rotated) |
| `RAZORPAY_KEY_SECRET` | (rotated) |
| `RAZORPAY_WEBHOOK_SECRET` | (rotated) |

---

## Phase 9 — Google OAuth Redirect URIs

After DNS is live, add production URIs in Google Cloud Console:
1. Authorized JavaScript origins: `https://littlelootgifts.com`
2. Authorized redirect URIs: `https://littlelootgifts.com/api/auth/callback/google`

---

## Phase 10 — Razorpay Webhook Endpoint

In Razorpay Dashboard → Settings → Webhooks:
1. Add endpoint: `https://api.littlelootgifts.com/api/payment/webhook`
2. Select events: `payment.captured`, `payment.failed`, `refund.processed`
3. Ensure the webhook secret matches `RAZORPAY_WEBHOOK_SECRET` in Secrets Manager

---

## Phase 11 — First Deploy Checklist

Before going live:
- [ ] All secrets rotated (Phase 0 above)
- [ ] `.env` removed from git history
- [ ] RDS available and reachable from App Runner (VPC security groups)
- [ ] `alembic upgrade head` run successfully against production RDS
- [ ] `/healthz` returns `{"status":"ok"}` from App Runner URL
- [ ] Frontend loads at Vercel preview URL
- [ ] Google login works end-to-end
- [ ] A test Razorpay payment completes (test mode)
- [ ] Admin panel accessible at `/admin`
- [ ] DNS propagated and TLS certificates issued

---

## Notes

- **Never commit `.env` files.** Add `*.env`, `.env*`, `final-project/.env` to `.gitignore`.
- **Razorpay is in test mode.** Switch to Live Mode only after full QA pass and before first real transaction.
- **RDS region**: Terraform will create RDS in `ap-south-2` (Hyderabad). App Runner will run in `ap-south-1` (Mumbai). Cross-region RDS access requires either VPC peering or using `ap-south-1` for both. Confirm App Runner is available in `ap-south-2` before deploying — if not, change `app_runner_region` Terraform variable to `ap-south-1`.
