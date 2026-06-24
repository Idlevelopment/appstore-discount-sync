#!/usr/bin/env python3
"""App Store Connect IAP Pricing Updater

For each rule in the pricing rules file:
  For every country/territory:
    price(target, territory) = price(source, territory) * factor

where factor is either (1 - discountPercent / 100) or an explicit multiplier
(e.g. 3 to make the target 3x the source). Exactly one of the two fields must
be set per rule.

The factor is applied individually per territory using the source IAP's actual
local price in each country. Apple's price tiers differ per territory, so the
script finds the closest available tier to the computed price.
All territories are set as explicit manual prices in one request.

How to find an IAP's Apple ID:
  App Store Connect → your app → Monetization → In-App Purchases (or Subscriptions)
  → click the product → the numeric ID is in the URL or shown in the sidebar.

Required environment variables:
  APPLE_KEY_ID       — API key ID (App Store Connect → Users and Access → Integrations → Keys)
  APPLE_ISSUER_ID    — Issuer ID (same page, shown at the top)
  APPLE_PRIVATE_KEY  — Full contents of the downloaded .p8 file (newlines preserved)

Optional:
  DRY_RUN            — Set to "true" to log actions without modifying anything
  RULES_PATH         — Path to the pricing rules JSON file (default: appstore-pricing-rules.json)

Note on permissions: the API key must have at least the 'App Manager' role.
"""

import json
import jwt as pyjwt
import os
import requests
import sys
import time
from pathlib import Path

BASE_URL = "https://api.appstoreconnect.apple.com"

RULES_PATH = Path(os.environ.get("RULES_PATH", "appstore-pricing-rules.json"))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def generate_token(key_id: str, issuer_id: str, private_key: str) -> str:
    payload = {
        "iss": issuer_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 1200,
        "aud": "appstoreconnect-v1",
    }
    return pyjwt.encode(
        payload,
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


def auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def fetch_all(url: str, hdrs: dict, params: dict | None = None) -> list:
    """Follow pagination and return all `data` items."""
    items: list = []
    next_url: str | None = url
    while next_url:
        resp = requests.get(next_url, headers=hdrs, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        items.extend(body.get("data", []))
        next_url = body.get("links", {}).get("next")
        params = None
    return items


def fetch_all_with_includes(
    url: str, hdrs: dict, params: dict | None = None
) -> tuple[list, dict]:
    """Follow pagination, returning (all data items, included resources keyed by id)."""
    all_data: list = []
    all_included: dict = {}
    next_url: str | None = url
    while next_url:
        resp = requests.get(next_url, headers=hdrs, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        all_data.extend(body.get("data", []))
        for item in body.get("included", []):
            all_included[item["id"]] = item
        next_url = body.get("links", {}).get("next")
        params = None
    return all_data, all_included


def api_get(url: str, hdrs: dict, params: dict | None = None) -> dict:
    resp = requests.get(url, headers=hdrs, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Price schedule
# ---------------------------------------------------------------------------

def get_price_schedule(hdrs: dict, iap_id: str) -> dict:
    """Return the full InAppPurchasePriceSchedule resource for an IAP."""
    body = api_get(f"{BASE_URL}/v2/inAppPurchases/{iap_id}/iapPriceSchedule", hdrs)
    schedule = body.get("data")
    if not schedule:
        raise LookupError(
            f"No price schedule found for IAP {iap_id}. "
            "Make sure the IAP exists and has a price set in App Store Connect."
        )
    return schedule


def get_base_territory(hdrs: dict, schedule: dict) -> str:
    """Fetch the base territory ID (e.g. 'USA', 'GBR') for a price schedule.

    The baseTerritory relationship is link-only (no inline data), so we follow
    the 'related' link to get the actual territory resource.
    """
    related_url = (
        schedule.get("relationships", {})
        .get("baseTerritory", {})
        .get("links", {})
        .get("related")
    )
    if not related_url:
        raise LookupError(
            f"Base territory link not found in price schedule {schedule.get('id')}."
        )
    body = api_get(related_url, hdrs)
    territory_id = body.get("data", {}).get("id")
    if not territory_id:
        raise LookupError(
            f"Base territory data not found at {related_url}."
        )
    return territory_id


def _prices_from_relationship(
    hdrs: dict, schedule_id: str, relationship: str
) -> dict[str, float]:
    """Fetch {territory_id: customer_price} from a manualPrices or automaticPrices endpoint.

    Both inAppPurchasePricePoint (for customerPrice) and territory are included
    in a single request so no extra calls are needed.
    """
    result: dict[str, float] = {}
    print(f"  Fetching {relationship}...", end=" ", flush=True)
    try:
        data, included = fetch_all_with_includes(
            f"{BASE_URL}/v1/inAppPurchasePriceSchedules/{schedule_id}/{relationship}",
            hdrs,
            {"include": "inAppPurchasePricePoint,territory", "limit": 200},
        )
    except requests.HTTPError as exc:
        if exc.response.status_code == 404:
            print("404 (none)")
            return result
        raise
    print(f"{len(data)} entries")

    for price_resource in data:
        territory_id = (
            price_resource.get("relationships", {})
            .get("territory", {})
            .get("data", {})
            .get("id")
        )
        if not territory_id:
            continue
        pp_id = price_resource["relationships"]["inAppPurchasePricePoint"]["data"]["id"]
        pp = included.get(pp_id)
        if not pp:
            continue
        result[territory_id] = float(pp["attributes"]["customerPrice"])

    return result


def get_all_prices(hdrs: dict, schedule_id: str) -> dict[str, float]:
    """Return {territory_id: customer_price} for every territory.

    Fetches automatic prices first (Apple-calculated for all countries), then
    manual prices on top so that any explicit overrides take precedence.
    """
    prices = _prices_from_relationship(hdrs, schedule_id, "automaticPrices")
    prices.update(_prices_from_relationship(hdrs, schedule_id, "manualPrices"))

    if not prices:
        raise LookupError(
            f"No prices found in schedule {schedule_id}. "
            "The IAP may not have any price set yet."
        )
    return prices


# ---------------------------------------------------------------------------
# Price points
# ---------------------------------------------------------------------------

def get_price_points_for_territory(
    hdrs: dict, iap_id: str, territory: str
) -> list[dict]:
    """Return all available price points for an IAP in a specific territory.

    Uses GET /v2/inAppPurchases/{id}/pricePoints?filter[territory]=TERRITORY.
    Results are sorted ascending by customerPrice.
    """
    points = fetch_all(
        f"{BASE_URL}/v2/inAppPurchases/{iap_id}/pricePoints",
        hdrs,
        {"filter[territory]": territory, "limit": 8000},
    )
    return sorted(
        [{"id": p["id"], "price": float(p["attributes"]["customerPrice"])} for p in points],
        key=lambda x: x["price"],
    )


VALID_ROUNDING = ("nearest", "up", "down")


def best_price_point(
    points: list[dict], target: float, rounding: str = "nearest"
) -> dict:
    """Return the chosen price point for target under the given rounding strategy.

    nearest — closest tier, above or below (default).
    up      — cheapest tier >= target; falls back to the highest tier if every
              tier is below target.
    down    — dearest tier <= target; falls back to the lowest tier if every
              tier is above target.

    `points` is assumed sorted ascending by price (as returned by
    get_price_points_for_territory).
    """
    if rounding == "up":
        return next((p for p in points if p["price"] >= target), points[-1])
    if rounding == "down":
        return next((p for p in reversed(points) if p["price"] <= target), points[0])
    return min(points, key=lambda p: abs(p["price"] - target))


# ---------------------------------------------------------------------------
# Bulk price update
# ---------------------------------------------------------------------------

def apply_prices_bulk(
    hdrs: dict,
    iap_id: str,
    base_territory: str,
    price_point_map: dict[str, str],  # {territory_id: price_point_id}
    dry_run: bool,
) -> None:
    """POST a new price schedule setting every territory as an explicit manual price.

    Endpoint: POST /v1/inAppPurchasePriceSchedules
    """
    if dry_run:
        print(f"  [DRY RUN] Would POST /v1/inAppPurchasePriceSchedules "
              f"with {len(price_point_map)} territories")
        return

    manual_prices_refs = []
    included_resources = []

    for territory_id, pp_id in price_point_map.items():
        # Local reference ID — must be the same string in both data and included.
        ref = f"${{price-{territory_id}}}"
        manual_prices_refs.append({"type": "inAppPurchasePrices", "id": ref})
        included_resources.append(
            {
                "type": "inAppPurchasePrices",
                "id": ref,
                "attributes": {"startDate": None},
                "relationships": {
                    "inAppPurchasePricePoint": {
                        "data": {"type": "inAppPurchasePricePoints", "id": pp_id}
                    },
                },
            }
        )

    body = {
        "data": {
            "type": "inAppPurchasePriceSchedules",
            "attributes": {},
            "relationships": {
                "inAppPurchase": {"data": {"type": "inAppPurchases", "id": iap_id}},
                "baseTerritory": {"data": {"type": "territories", "id": base_territory}},
                "manualPrices": {"data": manual_prices_refs},
            },
        },
        "included": included_resources,
    }

    resp = requests.post(
        f"{BASE_URL}/v1/inAppPurchasePriceSchedules",
        headers=hdrs,
        json=body,
        timeout=60,
    )
    if not resp.ok:
        print(f"  API error {resp.status_code}: {resp.text}", file=sys.stderr)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Rule processing
# ---------------------------------------------------------------------------

def resolve_multiplier(rule: dict) -> tuple[float, str]:
    """Return (multiplier, label) from a rule's discountPercent or multiplier.

    Exactly one of the two fields must be present. The multiplier is the factor
    applied to the source price: target = source * multiplier.
    """
    has_discount = "discountPercent" in rule
    has_multiplier = "multiplier" in rule

    if has_discount and has_multiplier:
        raise ValueError(
            "Specify either 'discountPercent' or 'multiplier', not both."
        )
    if not has_discount and not has_multiplier:
        raise ValueError(
            "Each rule must specify either 'discountPercent' or 'multiplier'."
        )

    if has_discount:
        discount = rule["discountPercent"]
        if not (0 < discount < 100):
            raise ValueError(
                f"discountPercent must be between 0 and 100 (exclusive), got {discount}"
            )
        return 1 - discount / 100, f"discount={discount}%"

    multiplier = rule["multiplier"]
    if multiplier <= 0:
        raise ValueError(f"multiplier must be greater than 0, got {multiplier}")
    return multiplier, f"multiplier={multiplier}"


def resolve_rounding(rule: dict) -> str:
    """Return the tier-rounding strategy for a rule (default 'nearest')."""
    rounding = rule.get("rounding", "nearest")
    if rounding not in VALID_ROUNDING:
        raise ValueError(
            f"rounding must be one of {VALID_ROUNDING}, got {rounding!r}"
        )
    return rounding


def process_rule(hdrs: dict, rule: dict, dry_run: bool) -> None:
    src_iap_id = rule["sourceIapId"]
    tgt_iap_id = rule["targetIapId"]
    multiplier, label = resolve_multiplier(rule)
    rounding = resolve_rounding(rule)

    print(f"\nRule: [{src_iap_id}] → [{tgt_iap_id}]  {label}  rounding={rounding}")

    # --- Source: read schedule, base territory, and prices for every country ---
    src_schedule = get_price_schedule(hdrs, src_iap_id)
    base_territory = get_base_territory(hdrs, src_schedule)
    src_prices = get_all_prices(hdrs, src_schedule["id"])
    print(f"  Base territory : {base_territory}")
    print(f"  Source territories: {len(src_prices)}")

    # --- Per-territory: fetch target price points, apply discount, pick best tier ---
    price_point_map: dict[str, str] = {}
    price_log: list[tuple] = []  # (territory, src_price, target_price, chosen_price)
    skipped: list[str] = []

    for i, (territory_id, src_price) in enumerate(src_prices.items(), 1):
        target_price = round(src_price * multiplier, 2)
        print(f"  [{i}/{len(src_prices)}] {territory_id}: {src_price} → {target_price}",
              end="\r", flush=True)

        points = get_price_points_for_territory(hdrs, tgt_iap_id, territory_id)
        if not points:
            skipped.append(territory_id)
            continue

        chosen = best_price_point(points, target_price, rounding)
        price_point_map[territory_id] = chosen["id"]
        price_log.append((territory_id, src_price, target_price, chosen["price"]))

    print()  # newline after progress line

    if skipped:
        print(
            f"  WARNING: no price points for {len(skipped)} territories "
            f"(skipped): {', '.join(skipped[:10])}{'...' if len(skipped) > 10 else ''}",
            file=sys.stderr,
        )

    if dry_run:
        print(f"\n  {'Territory':<10} {'Source':>10} {'Target':>10} {'Chosen':>10}")
        print(f"  {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")
        for territory_id, src_price, target_price, chosen_price in price_log:
            flag = " !" if abs(chosen_price - target_price) > 0.01 else ""
            print(f"  {territory_id:<10} {src_price:>10.2f} {target_price:>10.2f} {chosen_price:>10.2f}{flag}")
        print()

    print(f"  Applying prices for {len(price_point_map)} territories...")
    apply_prices_bulk(hdrs, tgt_iap_id, base_territory, price_point_map, dry_run)

    suffix = " [DRY RUN]" if dry_run else ""
    print(f"  ✓ IAP {tgt_iap_id} updated for {len(price_point_map)} territories{suffix}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    key_id = os.environ["APPLE_KEY_ID"]
    issuer_id = os.environ["APPLE_ISSUER_ID"]
    private_key = os.environ["APPLE_PRIVATE_KEY"].replace("\\n", "\n")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    with open(RULES_PATH) as f:
        rules: list[dict] = json.load(f)

    if not rules:
        print("No pricing rules defined. Add entries to your pricing rules file.")
        return

    if dry_run:
        print("=== DRY RUN mode — no changes will be made ===\n")

    token = generate_token(key_id, issuer_id, private_key)
    hdrs = auth_headers(token)

    errors: list[str] = []
    for rule in rules:
        try:
            process_rule(hdrs, rule, dry_run)
        except Exception as exc:
            msg = f"FAILED [{rule.get('sourceIapId')} → {rule.get('targetIapId')}]: {exc}"
            print(f"\nERROR: {msg}", file=sys.stderr)
            errors.append(msg)

    print()
    if errors:
        print(f"{len(errors)} rule(s) failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    mode = "DRY RUN — " if dry_run else ""
    print(f"{mode}All {len(rules)} rule(s) applied successfully.")


if __name__ == "__main__":
    main()
