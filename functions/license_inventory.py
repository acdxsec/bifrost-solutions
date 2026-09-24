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


async def _organization_name(org_id: str, fallback: str | None) -> str:
    try:
        org = await organizations.get(org_id)
        return org.name
    except Exception:
        return fallback or org_id


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

        response = await client.get(
            GRAPH_URL,
            headers={
                "Authorization": f"Bearer {microsoft.oauth.access_token}",
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
                    "subscriptionCount": len(subscription_ids),
                }
            )

        return {
            "organizationId": org_id,
            "organizationName": org_name,
            "tenantId": tenant_id,
            "licenses": rows,
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
    }
