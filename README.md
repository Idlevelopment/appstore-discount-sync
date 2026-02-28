# App Store IAP Pricing Updater

A GitHub Action that automatically syncs App Store in-app purchase prices across products with a configurable discount — applied individually for every country using Apple's actual local price tiers.

## Why this exists

If you have two IAPs where one should always be priced at a discount relative to the other, Apple's built-in price propagation won't reliably maintain that relationship. When you set a price in one territory and let Apple auto-calculate the rest, it snaps each country to whatever tier it considers appropriate — which can result in the discount percentage varying significantly from country to country, or disappearing entirely in some territories.

This action works around that by reading the source IAP's **actual local price in every territory**, computing the discounted amount, and explicitly setting the closest price tier on the target IAP for each country individually. The result is a consistent, predictable discount everywhere Apple sells your app.

## How it works

For each rule you provide in the file, the action computes the discounted price as:

```
price(target, territory) = price(source, territory) × (1 − discountPercent / 100)
```

The script reads the source IAP's current price in **every territory** (~175 countries), applies the discount, then picks the **closest available Apple price tier** for the target IAP in that territory. All territories are updated in a single API request.

## Setup

### 1. Create an App Store Connect API key

Go to **App Store Connect → Users and Access → Integrations → App Store Connect API** and create a key with the **App Manager** role. Download the `.p8` file — you can only download it once.

### 2. Add GitHub Secrets

| Secret | Value |
|---|---|
| `APPLE_KEY_ID` | The key ID shown on the Keys page |
| `APPLE_ISSUER_ID` | The Issuer ID shown at the top of the Keys page |
| `APPLE_PRIVATE_KEY` | Full contents of the `.p8` file, including `-----BEGIN PRIVATE KEY-----` header/footer |

### 3. Create your pricing rules file

Add `appstore-pricing-rules.json` to the root of your repository:

```json
[
  {
    "sourceIapId": "6851376914",
    "targetIapId": "6581351960",
    "discountPercent": 10
  }
]
```

Multiple rules are supported — each runs independently and failures don't block the rest.

#### Finding an IAP's numeric Apple ID

**App Store Connect → Apps → your app → Monetization → In-App Purchases** → click the product. The numeric Apple ID appears in the URL and in the IAP description.

### 4. Add the workflow

```yaml
name: Update App Store IAP Pricing

on:
  schedule:
    - cron: '0 10 * * 1'   # every Monday at 10:00 UTC
  workflow_dispatch:
    inputs:
      dry_run:
        description: 'Dry run (log actions without changing anything)'
        type: boolean
        default: false

permissions:
  contents: read

jobs:
  update-pricing:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - uses: Idlevelopment/appstore-discount-sync@v1.0
        with:
          apple-key-id: ${{ secrets.APPLE_KEY_ID }}
          apple-issuer-id: ${{ secrets.APPLE_ISSUER_ID }}
          apple-private-key: ${{ secrets.APPLE_PRIVATE_KEY }}
          dry-run: ${{ inputs.dry_run || 'false' }}
```

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `apple-key-id` | Yes | — | App Store Connect API Key ID |
| `apple-issuer-id` | Yes | — | App Store Connect Issuer ID |
| `apple-private-key` | Yes | — | Full contents of the `.p8` key file |
| `rules-path` | No | `appstore-pricing-rules.json` | Path to rules file, relative to repo root |
| `dry-run` | No | `false` | Log planned changes without modifying App Store Connect |

## Pricing rules format

```json
[
  {
    "sourceIapId": "6851376914",
    "targetIapId": "6581351960",
    "discountPercent": 10
  },
  {
    "sourceIapId": "6618358137",
    "targetIapId": "6589135761",
    "discountPercent": 25
  }
]
```

| Field | Type | Description |
|---|---|---|
| `sourceIapId` | string | Numeric Apple ID of the IAP to read prices from |
| `targetIapId` | string | Numeric Apple ID of the IAP to update |
| `discountPercent` | number | Discount percentage (exclusive: 0–100) |

## Custom rules file path

If you want to keep the rules file in a different location:

```yaml
- uses: Idlevelopment/appstore-discount-sync@v1.0
  with:
    apple-key-id: ${{ secrets.APPLE_KEY_ID }}
    apple-issuer-id: ${{ secrets.APPLE_ISSUER_ID }}
    apple-private-key: ${{ secrets.APPLE_PRIVATE_KEY }}
    rules-path: '.github/pricing/my-rules.json'
```

## Dry run

Run the workflow manually from **Actions → Update App Store IAP Pricing → Run workflow** with **Dry run** checked. The action will print a full table of per-territory prices without making any changes:

```
  Territory     Source     Target     Chosen
  ---------- ---------- ---------- ----------
  AFG              0.99       0.89       0.89
  ARG            999.99     899.99     899.99
  AUS              1.99       1.79       1.79
  ...
```

Rows marked with `!` are territories where the exact target price was not available and the closest tier was used instead.

## Notes

- The API key requires the **App Manager** role. Lower roles (e.g. Developer) cannot read price points and will receive a 403 error.
- Prices are read from both `automaticPrices` (Apple-calculated) and `manualPrices` (explicit overrides). Manual prices take precedence for the source.
- Each run sets **all territories as explicit manual prices** on the target IAP, overwriting any previous schedule.
- Processing ~175 territories takes approximately 3–5 minutes due to per-territory price point lookups.
