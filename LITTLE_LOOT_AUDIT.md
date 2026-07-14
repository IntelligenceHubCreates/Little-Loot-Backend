# Little Loot — Full-Stack Audit Report
**Date:** 2026-07-10  
**Scope:** `Backend/Silvee-Backend/` (FastAPI) + `final-project/` (Next.js)  
**Phase 0 — Findings, Design Tokens, Representative Page Redesign**

---

## LEGEND
| Badge | Meaning |
|-------|---------|
| 🔴 **BLOCKING** | Will break production or is an active security vulnerability. Must fix before go-live. |
| 🟠 **IMPORTANT** | Breaks a feature, violates good practice, or carries real risk. Fix in the next sprint. |
| 🟡 **NICE-TO-HAVE** | Polish, dead code, or missing-but-tolerable. Schedule when bandwidth allows. |

---

## A — INFRASTRUCTURE & BACKEND

### 🔴 A-1 · Plaintext admin password in source code
**File:** `Backend/Silvee-Backend/app_entrypoint.sh:21`
```sh
INSERT INTO users … VALUES ('qualityagency79@gmail.com', true, '$2b$12$cJ…', 1);  #mynewbackendtestedis
```
The cleartext admin password is committed as an inline comment next to the bcrypt hash. Anyone with repo access or access to a built Docker image has the production admin password.  
**Fix:** Remove the comment immediately. Rotate the admin password. Seed admin via a separate, gitignored script or environment variable.

---

### 🔴 A-2 · Docker image is broken without the dev bind-mount
**File:** `Backend/Silvee-Backend/Dockerfile`
```dockerfile
COPY app /app/          # ← copies app/ CONTENTS into /app/ root
# alembic.ini and alembic/ are never copied
```
`COPY app /app/` places everything in `app/` directly at `/app/`, not at `/app/app/`. Every `from app.xxx import …` fails. Additionally `alembic.ini` and the `alembic/` migrations directory are never `COPY`-ed, so `alembic upgrade head` in `app_entrypoint.sh` crashes silently (no `set -e`). This is masked in development by `volumes: - .:/app` in compose.yml but **any production deploy** without the bind-mount fails at startup.  
**Fix:**
```dockerfile
COPY app /app/app/
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic/
COPY app_entrypoint.sh wait-for-it.sh /app/
```

---

### 🔴 A-3 · Production runs `uvicorn --reload`
**File:** `Backend/Silvee-Backend/app_entrypoint.sh:49`
```sh
echo "Running in production mode"
uvicorn app.main:server --host=0.0.0.0 --reload   # ← reload = dev mode
```
`--reload` spins up a watchdog that monitors the filesystem for changes and restarts the server. In production: (a) it wastes CPU/memory, (b) it can be exploited if an attacker can place files on the container filesystem, (c) it masks legitimate crashes as "reloads". The echo message says "production" but the flag says otherwise.  
**Fix:** Remove `--reload`. Add `-w 2` (workers) or use gunicorn as a process manager.

---

### 🔴 A-4 · No `set -e` — migration failures are silently swallowed
**File:** `Backend/Silvee-Backend/app_entrypoint.sh`  
The entrypoint has no `set -e`. If `alembic upgrade head` fails (conflict, missing table, network partition), the script continues and starts uvicorn on a broken schema — resulting in 500 errors on every request that touches the DB, or worse, silent data corruption.  
**Fix:** Add `set -e` on line 1 immediately after the shebang.

---

### 🔴 A-5 · Admin seed check uses `WHERE id = 1` on a UUID column
**File:** `Backend/Silvee-Backend/app_entrypoint.sh:20-28`
```sh
RECORD_CHECK_QUERY="SELECT * FROM users WHERE id = 1;"
```
`users.id` is a UUID (`gen_random_uuid()`). This query always returns empty → the INSERT always runs → it succeeds on the first startup and throws a duplicate-email unique violation on every subsequent restart. Container restarts may log errors indefinitely.  
**Fix:** `SELECT 1 FROM users WHERE email = 'qualityagency79@gmail.com' LIMIT 1;`  
(And better still: use an Alembic data migration rather than shell-level seeding.)

---

### 🟠 A-6 · No health endpoint; no compose HEALTHCHECK
**File:** `Backend/Silvee-Backend/app/main.py`, `Backend/Silvee-Backend/compose.yml`  
`GET /` returns a welcome message, not a health probe. `compose.yml` has no `HEALTHCHECK` directive. Load balancers, Kubernetes probes, and container orchestrators cannot determine if the app is live or stuck.  
**Fix:** Add `GET /healthz` that queries the DB and returns `{"status":"ok"}`. Add `HEALTHCHECK` to Dockerfile and compose.yml.

---

### 🟠 A-7 · No rate limiting on auth endpoints
**Endpoints:** `POST /api/user/login`, `POST /api/user/register`, `POST /api/user/forgot-password`  
None of these have any rate limiting or IP-based throttling. `/login` can be brute-forced. `/forgot-password` can be used to flood any email address with reset requests (despite the anti-enumeration 200 response, repeated calls do generate and store new tokens).  
**Fix:** Add `slowapi` (or `fastapi-limiter`) middleware. Suggested limits: login 5/min/IP, forgot-password 3/min/IP, register 10/min/IP.

---

### 🟠 A-8 · `RESEND_API_KEY` absent → silent email failure in "production"
**File:** `Backend/Silvee-Backend/app/users/routers.py:240-242`  
If `RESEND_API_KEY` is not configured, the forgot-password endpoint logs the reset link to the server console and returns HTTP 200 with the "check your inbox" message. In a production environment where users see the success screen but receive no email, this is a broken feature that appears to work.  
**Fix:** In `settings.py`, make `resend_api_key` required when `ENVIRONMENT=production`. Optionally surface a `503 Service Unavailable` in production.

---

### 🟠 A-9 · `ALLOWED_ORIGINS` defaults to localhost; breaks deployed API
**File:** `Backend/Silvee-Backend/app/main.py:59-63`  
If `ALLOWED_ORIGINS` env var is not set, CORS allows only `http://localhost:3000`. A production frontend at `https://littleloot.in` will be blocked by browsers on every API call.  
**Fix:** Set `ALLOWED_ORIGINS=https://littleloot.in,https://www.littleloot.in` in `.env.production`. Validate the variable is set when `IS_PRODUCTION=true`.

---

### 🟠 A-10 · `PaymentOrder` model defined inline in a router (not tracked by Alembic)
**File:** `Backend/Silvee-Backend/app/payments/routers.py:35-51`  
`PaymentOrder` and `PasswordResetToken` are not in the Alembic-tracked `app/models.py`. They exist via `Base.metadata.create_all()` at startup, but any column additions or alterations will never show up in `alembic revision --autogenerate`. Schema drift will silently accumulate.  
**Fix:** Move both models to their respective `models.py` files (or import them into `app/models.py` so Alembic can see them).

---

### 🟠 A-11 · `datetime.utcnow()` deprecated throughout payments router
**File:** `Backend/Silvee-Backend/app/payments/routers.py` (multiple lines)  
Python 3.12 deprecated `datetime.utcnow()`. Already migrated in `users/routers.py` (uses `datetime.now(timezone.utc)`), but `payments/routers.py` still calls `datetime.utcnow()` in at least 3 places including `PaymentOrder.created_at` and `paid_at`.  
**Fix:** Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)`.

---

### 🟡 A-12 · No `Content-Security-Policy` header
**File:** `Backend/Silvee-Backend/app/main.py:76-85`  
Security-headers middleware sets X-Frame-Options, X-Content-Type-Options, HSTS, Referrer-Policy, and Permissions-Policy, but no `Content-Security-Policy`. This leaves XSS mitigation to browser defaults.  
**Fix:** Add a restrictive CSP. Minimum: `default-src 'self'; img-src 'self' data: res.cloudinary.com; script-src 'self' 'unsafe-inline' checkout.razorpay.com;`

---

### 🟡 A-13 · Order-confirmation email never sent
**File:** `Backend/Silvee-Backend/app/notifications/` (module exists but isn't called from payment flow)  
After a successful `POST /api/payments/verify`, no customer email is dispatched. The `notifications/` router and dispatcher exist but `verify_payment()` does not call them.  
**Fix:** After `db_order = svc_create_order(...)`, dispatch an order-confirmation email via the notifications dispatcher.

---

## B — FEATURE WIRING

### 🔴 B-1 · Frontend Terms of Service & Privacy Policy links are dead `#`
**File:** `final-project/src/components/pages/SignupPage.tsx:290-293`
```tsx
<Link href="#">Terms of Service</Link>
&nbsp;and&nbsp;
<Link href="#">Privacy Policy</Link>
```
These are required disclosures for any e-commerce site. Clicking them goes nowhere. Legally, you must have real pages (or at minimum PDFs) that users can read before consenting.  
**Fix:** Create `/terms` and `/privacy` pages (even a minimal policy). Update links.

---

### 🟠 B-2 · Password minimum inconsistency: signup allows 6 chars, reset-password requires 8
**Files:** `final-project/src/components/pages/SignupPage.tsx:58` (6-char min), `Backend/.../users/routers.py:290` (8-char min)  
A user who registers with a 7-character password and later resets it via the forgot-password flow will hit a backend 400 error. The UI's strength meter also considers 6 chars "fair" when the backend would reject it.  
**Fix:** Unify to 8 characters everywhere. Update `SignupPage.tsx` validation and the `getStrength()` function.

---

### 🟠 B-3 · `NEXTAUTH_SECRET` not validated; missing in production env docs
**File:** `final-project/` — no `.env.example` for frontend  
Without `NEXTAUTH_SECRET`, NextAuth in development uses a per-session default. In production this means sessions cannot be verified across restarts, users are logged out unexpectedly, and Credentials Provider JWTs are effectively insecure.  
**Fix:** Require `NEXTAUTH_SECRET` in `final-project/.env` or Vercel environment config. Add a startup check (e.g., `next.config.js` that throws if missing in production).

---

### 🟠 B-4 · `NEXTAUTH_URL` not set — OAuth callbacks will break in production
**File:** `final-project/` — no `NEXTAUTH_URL` env var documented  
Google OAuth callback URL is derived from `NEXTAUTH_URL`. Without it in a deployed environment, the redirect after Google sign-in goes to `localhost:3000`.  
**Fix:** Set `NEXTAUTH_URL=https://littleloot.in` in production env.

---

### 🟡 B-5 · Track-order page has no email notification trigger
**File:** `final-project/src/app/track-order/` (page exists), notifications module exists  
Users can visit `/track-order` to look up status manually, but there's no automatic dispatch when order status changes (confirmed → shipped → delivered).  
**Fix:** Wire `notifications/dispatcher.py` into `update_order_status_admin()` in `orders/services.py`.

---

### 🟡 B-6 · Cart data not cleared after successful payment
**Symptom:** After `POST /api/payments/verify` returns success, the frontend should call the cart-clear API. Whether this happens depends on `CartPage.tsx`/checkout flow — not confirmed.  
**Action:** Verify `CartContext` clears on successful order creation. If not, add explicit clear after verify resolves.

---

## C — GO-LIVE ESSENTIALS

### 🟠 C-1 · No `sitemap.xml` or `robots.txt`
**File:** `final-project/src/app/` — neither file exists  
Google cannot crawl product/category pages efficiently without a sitemap. Without `robots.txt`, all bots have unrestricted access including admin routes.  
**Fix:** Add `final-project/src/app/sitemap.ts` (Next.js App Router sitemap generator) for dynamic product pages. Add `final-project/public/robots.txt` disallowing `/admin`, `/api`, `/account`.

---

### 🟠 C-2 · Category and product pages lack per-page SEO metadata
**File:** `final-project/src/app/toys/`, `final-project/src/app/products/`, etc.  
Only `layout.tsx` exports global `metadata`. Category pages (`/toys`, `/bags`, `/stationery`) have no `generateMetadata()` export. Product pages likely also lack dynamic title/OG tags. Google will index them with the generic site title.  
**Fix:** Add `export async function generateMetadata({ params })` to category and product `page.tsx` files with descriptive titles, descriptions, and Open Graph image tags.

---

### 🟡 C-3 · No Open Graph / Twitter Card tags
**File:** `final-project/src/app/layout.tsx`  
No `openGraph` or `twitter` metadata block. Sharing a product link on WhatsApp/Instagram shows no preview image, title, or description.  
**Fix:** Add to `layout.tsx` metadata:
```ts
openGraph: {
  title: 'Little Loot — Kids & Stationery Store',
  description: '…',
  images: ['/og-image.png'],
  siteName: 'Little Loot',
},
```

---

### 🟡 C-4 · Favicon is a full-color logo PNG — will look bad at 16×16
**File:** `final-project/src/app/layout.tsx:27-30`  
`/Logo.png` (390×130px, full-color) is used as the favicon. Browsers scale it to 16×16/32×32 where it becomes illegible. Apple touch icon at 180×180 will also look blurry.  
**Fix:** Export a square 512×512 icon (just the mark/emblem, no wordmark) in `.ico`/`.svg` format. Use that as `icon`. Keep Logo.png for `apple-touch-icon` at 180×180 minimum.

---

## D — UI/UX STATE

### 🟡 D-1 · Login/Signup pages ship; all other pages still use legacy CSS
**Status:** LoginPage, SignupPage, ForgotPasswordPage, ResetPasswordPage, Header, Footer updated to premium design.  
**Pending (not yet done):** CartPage, CheckoutPage, ProductPage, CategoryPage, WishlistPage, AccountPage, TrackOrderPage — all still use original CSS (no design token system).  
**Note for Phase 1+:** Once design tokens are applied globally, these pages update with minimal per-page work.

---

### 🟡 D-2 · No `prefers-reduced-motion` guards on animations
**Files:** `LoginPage.module.css`, `SignupPage.module.css`  
Float animations (`@keyframes floatA/B/C`) and pulse animations run unconditionally. Users who have enabled "Reduce Motion" in their OS settings will still see full animations — a WCAG 2.1 (§2.3.3 Animation from Interactions) violation.  
**Fix:**
```css
@media (prefers-reduced-motion: reduce) {
  .stackCard, .floatBadge, .promoBadge { animation: none; }
}
```

---

### 🟡 D-3 · No loading skeleton or skeleton screens
**Status:** All data-fetching pages (ProductPage, CategoryPage, CartPage) show no skeleton while loading. UX feels "blank" on slow connections.  
**Fix (Phase 4):** Add `loading.tsx` files per Next.js App Router convention for at least category and product pages.

---

---

## DESIGN TOKEN SYSTEM

Based on the locked Little Loot brand palette extracted from `Logo.png`.

### Color Tokens
```css
:root {
  /* Brand palette */
  --color-primary:    #3B0F4E;   /* Plum — headers, primary actions, ink */
  --color-accent:     #FF4D6A;   /* Coral — CTAs, badges, links */
  --color-highlight:  #FFC61A;   /* Sunshine — promo badges, ratings, icons */
  --color-canvas:     #FFFDF9;   /* Warm white — page background */

  /* Surface & text */
  --color-surface:    #FFFFFF;
  --color-ink:        #3B0F4E;   /* Primary text */
  --color-body:       #5B4266;   /* Secondary text */
  --color-muted:      #8A7891;   /* Placeholder / tertiary */
  --color-border:     #EFE7EC;   /* Dividers, input borders */

  /* Semantic */
  --color-success:    #22C55E;
  --color-warning:    #F59E0B;
  --color-error:      #EF4444;
  --color-info:       #3B82F6;
}
```

### Typography Tokens
```css
:root {
  --font-display: 'Baloo 2', system-ui, sans-serif;  /* already loaded */
  --font-body:    'Nunito',  system-ui, sans-serif;  /* already loaded */

  /* Scale — Major Third (1.250) */
  --text-xs:   0.64rem;    /* 10px */
  --text-sm:   0.8rem;     /* 13px */
  --text-base: 1rem;       /* 16px */
  --text-md:   1.25rem;    /* 20px */
  --text-lg:   1.563rem;   /* 25px */
  --text-xl:   1.953rem;   /* 31px */
  --text-2xl:  2.441rem;   /* 39px */
  --text-3xl:  3.052rem;   /* 49px */
}
```

### Spacing Tokens
```css
:root {
  /* 4px base grid */
  --space-1:  4px;
  --space-2:  8px;
  --space-3:  12px;
  --space-4:  16px;
  --space-6:  24px;
  --space-8:  32px;
  --space-12: 48px;
  --space-16: 64px;
  --space-24: 96px;
}
```

### Border Radius Tokens
```css
:root {
  --radius-sm:   6px;
  --radius-md:   12px;
  --radius-lg:   16px;
  --radius-xl:   24px;
  --radius-2xl:  32px;
  --radius-pill: 999px;
}
```

### Shadow Tokens
```css
:root {
  --shadow-card:     0 2px 12px rgba(59,15,78, 0.07);
  --shadow-lifted:   0 8px 32px rgba(59,15,78, 0.12);
  --shadow-floating: 0 16px 48px rgba(59,15,78, 0.18);
  --shadow-glow-accent: 0 0 24px rgba(255,77,106, 0.28);
}
```

### Transition Tokens
```css
:root {
  --ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);
  --ease-spring:    cubic-bezier(0.34, 1.56, 0.64, 1);
  --duration-fast:  120ms;
  --duration-base:  200ms;
  --duration-slow:  350ms;
}
```

**Deployment:** Extract to `final-project/src/styles/tokens.css`, import in `globals.css`. All component CSS modules reference `var(--...)` from that point on.

---

## REPRESENTATIVE PAGE REDESIGN — `CheckoutPage`

The Checkout page is the highest-stakes UI in the app (it precedes payment). Current state: functional but visually inconsistent with the new Login/Signup premium design.

### Redesign Concept (in locked palette)

**Layout:** Two-column at ≥ 768px. Left = form (60%), Right = sticky order summary (40%).

**Step indicator** (top, full width):
- 3 steps: `Address → Payment → Confirm`
- Active step: coral underline + plum text; completed: filled plum circle with white checkmark
- Background: `var(--color-canvas)`

**Left panel — Address form:**
- White card, `border-radius: var(--radius-xl)`, `box-shadow: var(--shadow-card)`
- Section heading in `Baloo 2 700 20px var(--color-ink)`
- Input fields: 48px tall, `border: 1.5px solid var(--color-border)`, focused border `var(--color-primary)`; left icon in `var(--color-muted)`
- Error state: border `var(--color-error)`, soft red `background: #FFF5F5`

**Right panel — Order summary:**
- Sticky top-24px, `background: var(--color-canvas)`, `border: 1.5px solid var(--color-border)`, `border-radius: var(--radius-xl)`
- Product thumbnails (40×40px, `border-radius: var(--radius-sm)`) with name, qty, price
- Divider in `var(--color-border)`
- Subtotal / Delivery / Coupon discount rows in `var(--color-body) text-sm`
- **Total** in `Baloo 2 800 var(--text-lg) var(--color-ink)`
- Coupon input field with coral "Apply" pill button

**CTA — Place Order:**
- Full-width, 56px tall, `background: linear-gradient(135deg, var(--color-accent), #E03A55)`, `border-radius: var(--radius-pill)`
- Hover: `transform: translateY(-2px)`, `box-shadow: var(--shadow-glow-accent)`
- Loading state: white spinner (same as Login)
- Lock icon (🔒) + "Secure Checkout" caption below button in `var(--color-muted) text-xs`

**Trust bar** (below CTA):
```
[🛡 Secure Payment]  [↩ Easy Returns]  [📦 Fast Delivery]
```
Each badge: plum icon, `text-xs var(--color-muted)`, separated by `var(--color-border)` verticals.

**Coupon success state:** Sunshine `#FFC61A` background toast at top of summary panel, ✅ icon + "₹XX OFF applied".

---

## CHECKPOINT — STOP HERE

All findings above are documented. No fixes have been applied yet.

**Awaiting your approval to proceed to Phase 1 (Infrastructure Fixes) and beyond.**

Please confirm:
1. Which Blocking items to tackle first (A-1 through A-5 are all pre-launch blockers)
2. Whether to run Phases 1–5 sequentially or focus on specific areas
3. Any findings you want to demote or skip

Once you approve, Phase 1 begins immediately with A-2 (Dockerfile), A-3 (entrypoint), A-4 (set -e), A-5 (seed query), and A-1 (remove password comment).
