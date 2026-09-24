import asyncio
from datetime import datetime, timezone

import httpx

from bifrost import integrations, organizations, workflow


INTEGRATION_NAME = "Microsoft 365 Graph"
GRAPH_URL = (
    "https://graph.microsoft.com/v1.0/subscribedSkus"
    "?$select=id,accountName,accountId,appliesTo,capabilityStatus,"
    "consumedUnits,prepaidUnits,skuId,skuPartNumber,subscriptionIds"
)
SUBSCRIPTIONS_URL = (
    "https://graph.microsoft.com/beta/directory/subscriptions"
    "?$select=id,commerceSubscriptionId,ocpSubscriptionId,createdDateTime,"
    "nextLifecycleDateTime,skuId,skuPartNumber,status,totalLicenses,isTrial"
)


async def _organization_name(org_id: str, fallback: str | None) -> str:
    try:
        org = await organizations.get(org_id)
        return org.name
    except Exception:
        return fallback or org_id


def _subscription_key(value: object) -> str | None:
    if value is None:
        return None
    key = str(value).strip().lower()
    return key or None


async def _subscription_lifecycle_lookup(
    client: httpx.AsyncClient,
    access_token: str,
) -> tuple[dict[str, dict[str, dict | list[dict]]], str | None]:
    response = await client.get(
        SUBSCRIPTIONS_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )

    if response.status_code != 200:
        try:
            body = response.json()
            graph_error = body.get("error", {})
            detail = graph_error.get("message") or response.text
        except Exception:
            detail = response.text

        return {}, (
            "Microsoft Graph beta directory subscriptions returned "
            f"HTTP {response.status_code}: {detail}"
        )

    lookup: dict[str, dict[str, dict | list[dict]]] = {
        "by_id": {},
        "by_sku_id": {},
        "by_sku_part_number": {},
    }
    for subscription in response.json().get("value", []):
        normalized = {
            "id": subscription.get("id"),
            "commerceSubscriptionId": subscription.get("commerceSubscriptionId"),
            "ocpSubscriptionId": subscription.get("ocpSubscriptionId"),
            "createdDateTime": subscription.get("createdDateTime"),
            "nextLifecycleDateTime": subscription.get("nextLifecycleDateTime"),
            "status": subscription.get("status"),
            "totalLicenses": subscription.get("totalLicenses"),
            "isTrial": subscription.get("isTrial"),
        }
        for key_name in ("id", "commerceSubscriptionId", "ocpSubscriptionId"):
            key = _subscription_key(subscription.get(key_name))
            if key:
                lookup["by_id"][key] = normalized

        sku_id_key = _subscription_key(subscription.get("skuId"))
        if sku_id_key:
            lookup["by_sku_id"].setdefault(sku_id_key, []).append(normalized)

        sku_part_key = _subscription_key(subscription.get("skuPartNumber"))
        if sku_part_key:
            lookup["by_sku_part_number"].setdefault(sku_part_key, []).append(normalized)

    return lookup, None


def _subscription_detail_id(detail: dict) -> str | None:
    for key_name in ("commerceSubscriptionId", "ocpSubscriptionId", "id"):
        value = detail.get(key_name)
        if value:
            return str(value)
    return None


def _subscription_details_for_sku(sku: dict, subscription_lookup: dict) -> list[dict]:
    subscription_ids = sku.get("subscriptionIds") or []
    details: list[dict] = []
    seen: set[str] = set()

    def add_detail(subscription_id: object, detail: dict, match_source: str) -> None:
        detail_id = _subscription_detail_id(detail) or str(subscription_id)
        key = _subscription_key(detail_id)
        if key and key in seen:
            return
        if key:
            seen.add(key)
        details.append(
            {
                "subscriptionId": str(subscription_id or detail_id),
                **detail,
                "lifecycleMatchSource": match_source,
            }
        )

    for subscription_id in subscription_ids:
        key = _subscription_key(subscription_id)
        if not key:
            continue
        detail = subscription_lookup["by_id"].get(key)
        if detail:
            add_detail(subscription_id, detail, "subscriptionIds")

    if details:
        return details

    sku_id_key = _subscription_key(sku.get("skuId"))
    if sku_id_key:
        for detail in subscription_lookup["by_sku_id"].get(sku_id_key, []):
            add_detail(_subscription_detail_id(detail), detail, "skuId")

    if details:
        return details

    sku_part_key = _subscription_key(sku.get("skuPartNumber"))
    if sku_part_key:
        for detail in subscription_lookup["by_sku_part_number"].get(sku_part_key, []):
            add_detail(_subscription_detail_id(detail), detail, "skuPartNumber")

    return details


async def _fetch_tenant(mapping, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
    async with semaphore:
        org_id = str(mapping.organization_id)
        tenant_id = str(mapping.entity_id)
        org_name = await _organization_name(org_id, mapping.entity_name)

        microsoft = await integrations.get(
            INTEGRATION_NAME,
            scope=org_id,
        )

        if microsoft is None:
            raise RuntimeError("Microsoft 365 Graph integration is unavailable")

        if microsoft.oauth is None or not microsoft.oauth.access_token:
            raise RuntimeError("Microsoft Graph access token is unavailable")

        access_token = microsoft.oauth.access_token
        response = await client.get(
            GRAPH_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

        if response.status_code != 200:
            try:
                body = response.json()
                graph_error = body.get("error", {})
                detail = graph_error.get("message") or response.text
            except Exception:
                detail = response.text

            raise RuntimeError(
                f"Microsoft Graph returned HTTP {response.status_code}: {detail}"
            )

        subscription_lookup, lifecycle_error = await _subscription_lifecycle_lookup(
            client,
            access_token,
        )
        rows = []

        for sku in response.json().get("value", []):
            prepaid = sku.get("prepaidUnits") or {}

            enabled = int(prepaid.get("enabled") or 0)
            warning = int(prepaid.get("warning") or 0)
            suspended = int(prepaid.get("suspended") or 0)
            locked_out = int(prepaid.get("lockedOut") or 0)
            assigned = int(sku.get("consumedUnits") or 0)

            total_units = enabled + warning + suspended + locked_out
            available = max(enabled - assigned, 0)
            subscription_ids = sku.get("subscriptionIds") or []
            subscription_details = _subscription_details_for_sku(
                sku,
                subscription_lookup,
            )
            lifecycle_dates = [
                detail.get("nextLifecycleDateTime")
                for detail in subscription_details
                if detail.get("nextLifecycleDateTime")
            ]

            rows.append(
                {
                    "organizationId": org_id,
                    "organizationName": org_name,
                    "tenantId": tenant_id,
                    "accountName": sku.get("accountName"),
                    "accountId": sku.get("accountId"),
                    "appliesTo": sku.get("appliesTo"),
                    "skuId": sku.get("skuId"),
                    "skuPartNumber": sku.get("skuPartNumber"),
                    "status": sku.get("capabilityStatus"),
                    "totalUnits": total_units,
                    "enabledUnits": enabled,
                    "assignedUnits": assigned,
                    "availableUnits": available,
                    "warningUnits": warning,
                    "suspendedUnits": suspended,
                    "lockedOutUnits": locked_out,
                    "subscriptionIds": subscription_ids,
                    "subscriptionDetails": subscription_details,
                    "subscriptionCount": len(subscription_ids),
                    "nextLifecycleDateTime": min(lifecycle_dates)
                    if lifecycle_dates
                    else None,
                    "lifecycleLookupError": lifecycle_error,
                }
            )

        return {
            "organizationId": org_id,
            "organizationName": org_name,
            "tenantId": tenant_id,
            "licenses": rows,
            "lifecycleLookupError": lifecycle_error,
        }


@workflow
async def m365_license_inventory() -> dict:
    """Return Microsoft 365 license inventory across every mapped tenant."""

    mappings = await integrations.list_mappings(INTEGRATION_NAME)

    if mappings is None:
        raise RuntimeError(f"Integration '{INTEGRATION_NAME}' was not found")

    usable_mappings = [
        mapping
        for mapping in mappings
        if mapping.organization_id and mapping.entity_id
    ]

    semaphore = asyncio.Semaphore(8)
    licenses = []
    tenants = []
    errors = []
    warnings = []

    async with httpx.AsyncClient(timeout=60) as client:
        tasks = [
            _fetch_tenant(mapping, client, semaphore)
            for mapping in usable_mappings
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

    for mapping, result in zip(usable_mappings, results):
        if isinstance(result, Exception):
            errors.append(
                {
                    "organizationId": str(mapping.organization_id),
                    "organizationName": mapping.entity_name
                    or str(mapping.organization_id),
                    "tenantId": str(mapping.entity_id),
                    "error": str(result),
                }
            )
            continue

        tenants.append(
            {
                "organizationId": result["organizationId"],
                "organizationName": result["organizationName"],
                "tenantId": result["tenantId"],
                "skuCount": len(result["licenses"]),
            }
        )
        licenses.extend(result["licenses"])
        if result.get("lifecycleLookupError"):
            warnings.append(
                {
                    "organizationId": result["organizationId"],
                    "organizationName": result["organizationName"],
                    "tenantId": result["tenantId"],
                    "warning": result["lifecycleLookupError"],
                }
            )

    licenses.sort(
        key=lambda row: (
            (row.get("organizationName") or "").casefold(),
            (row.get("skuPartNumber") or "").casefold(),
        )
    )

    total_units = sum(row["totalUnits"] for row in licenses)
    enabled_units = sum(row["enabledUnits"] for row in licenses)
    assigned_units = sum(row["assignedUnits"] for row in licenses)
    available_units = sum(row["availableUnits"] for row in licenses)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "mappedTenants": len(usable_mappings),
            "tenantsSucceeded": len(tenants),
            "tenantsFailed": len(errors),
            "licenseSkuRows": len(licenses),
            "subscriptionReferences": sum(
                row["subscriptionCount"] for row in licenses
            ),
            "totalUnits": total_units,
            "enabledUnits": enabled_units,
            "assignedUnits": assigned_units,
            "availableUnits": available_units,
        },
        "tenants": tenants,
        "licenses": licenses,
        "errors": errors,
        "warnings": warnings,
    }
