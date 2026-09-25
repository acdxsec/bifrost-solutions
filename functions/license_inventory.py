import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from bifrost import integrations, tables, workflow


PARTNER_CENTER_INTEGRATION_NAME = "Microsoft Partner Center"
PARTNER_CENTER_BASE = "https://api.partnercenter.microsoft.com/v1"
TENANTS_TABLE = "m365_managed_tenants"
INVENTORY_TABLE = "m365_license_inventory"
HISTORY_TABLE = "m365_license_history"
SYNC_RUNS_TABLE = "m365_license_sync_runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _table_id(*parts: object) -> str:
    return ":".join(
        "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(part))
        for part in parts
        if part is not None and str(part).strip()
    )


def _items(payload: dict) -> list[dict]:
    value = payload.get("items") or payload.get("value") or []
    return value if isinstance(value, list) else []


def _next_link(payload: dict) -> str | None:
    if payload.get("@odata.nextLink"):
        return str(payload["@odata.nextLink"])
    links = payload.get("links") or {}
    next_link = links.get("next") if isinstance(links, dict) else None
    if isinstance(next_link, dict):
        return next_link.get("uri")
    if isinstance(next_link, str):
        return next_link
    return None


def _customer_name(customer: dict) -> str:
    profile = customer.get("companyProfile") or {}
    return (
        _text(customer.get("companyName"))
        or _text(customer.get("name"))
        or _text(profile.get("companyName"))
        or _text(profile.get("tenantId"))
        or _text(customer.get("id"))
        or "Unknown customer"
    )


def _customer_domain(customer: dict) -> str | None:
    profile = customer.get("companyProfile") or {}
    return (
        _text(customer.get("defaultDomain"))
        or _text(customer.get("domain"))
        or _text(profile.get("domain"))
    )


def _customer_tenant_id(customer: dict) -> str | None:
    profile = customer.get("companyProfile") or {}
    return (
        _text(customer.get("id"))
        or _text(customer.get("customerId"))
        or _text(customer.get("tenantId"))
        or _text(profile.get("tenantId"))
    )


def _subscription_key(value: object) -> str | None:
    text = _text(value)
    return text.lower() if text else None


def _subscription_lookup(subscriptions: list[dict]) -> dict[str, list[dict]]:
    lookup: dict[str, list[dict]] = {}
    for subscription in subscriptions:
        keys = [
            subscription.get("id"),
            subscription.get("subscriptionId"),
            subscription.get("offerId"),
            subscription.get("productId"),
            subscription.get("skuId"),
            subscription.get("productSkuId"),
            subscription.get("friendlyName"),
            subscription.get("offerName"),
            subscription.get("productName"),
        ]
        for value in keys:
            key = _subscription_key(value)
            if key:
                lookup.setdefault(key, []).append(subscription)
    return lookup


def _sku_keys(sku: dict) -> list[str]:
    product_sku = sku.get("productSku") or {}
    candidates = [
        sku.get("skuId"),
        sku.get("id"),
        sku.get("productSkuId"),
        sku.get("productId"),
        sku.get("skuPartNumber"),
        sku.get("name"),
        product_sku.get("id"),
        product_sku.get("skuId"),
        product_sku.get("productId"),
        product_sku.get("name"),
    ]
    keys = []
    for candidate in candidates:
        key = _subscription_key(candidate)
        if key and key not in keys:
            keys.append(key)
    return keys


def _subscription_details_for_partner_sku(
    sku: dict,
    subscriptions_by_key: dict[str, list[dict]],
) -> list[dict]:
    details = []
    seen = set()
    for key in _sku_keys(sku):
        for subscription in subscriptions_by_key.get(key, []):
            subscription_id = _text(
                subscription.get("id") or subscription.get("subscriptionId")
            )
            dedupe_key = subscription_id or key
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            details.append(
                {
                    "subscriptionId": subscription_id or key,
                    "id": subscription_id,
                    "commerceSubscriptionId": subscription_id,
                    "createdDateTime": subscription.get("creationDate")
                    or subscription.get("effectiveStartDate"),
                    "nextLifecycleDateTime": subscription.get("commitmentEndDate")
                    or subscription.get("renewalDate")
                    or subscription.get("endDate"),
                    "status": subscription.get("status"),
                    "totalLicenses": subscription.get("quantity"),
                    "isTrial": subscription.get("isTrial"),
                    "lifecycleMatchSource": "partnerCenterSubscription",
                }
            )
    return details


async def _partner_token() -> str:
    partner = await integrations.get(PARTNER_CENTER_INTEGRATION_NAME)
    if partner is None:
        raise RuntimeError(
            f"Integration '{PARTNER_CENTER_INTEGRATION_NAME}' is unavailable"
        )
    if partner.oauth is None or not partner.oauth.access_token:
        raise RuntimeError("Partner Center access token is unavailable")
    return partner.oauth.access_token


async def _partner_get(
    client: httpx.AsyncClient,
    token: str,
    path_or_url: str,
) -> dict:
    url = (
        path_or_url
        if path_or_url.startswith("https://")
        else f"{PARTNER_CENTER_BASE}{path_or_url}"
    )
    response = await client.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "MS-RequestId": str(uuid4()),
            "MS-CorrelationId": str(uuid4()),
        },
    )
    if response.status_code != 200:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(f"Partner Center returned HTTP {response.status_code}: {detail}")
    return response.json()


async def _partner_collection(
    client: httpx.AsyncClient,
    token: str,
    path: str,
    *,
    limit_pages: int = 100,
) -> list[dict]:
    rows = []
    next_url: str | None = path
    pages = 0
    while next_url and pages < limit_pages:
        payload = await _partner_get(client, token, next_url)
        rows.extend(_items(payload))
        next_url = _next_link(payload)
        pages += 1
    return rows


async def _fetch_partner_customer(
    customer: dict,
    client: httpx.AsyncClient,
    token: str,
    semaphore: asyncio.Semaphore,
    sync_id: str,
) -> dict:
    async with semaphore:
        tenant_id = _customer_tenant_id(customer)
        if not tenant_id:
            raise RuntimeError("Partner Center customer was missing an ID")

        customer_name = _customer_name(customer)
        subscribed_skus, subscriptions = await asyncio.gather(
            _partner_collection(client, token, f"/customers/{tenant_id}/subscribedskus"),
            _partner_collection(client, token, f"/customers/{tenant_id}/subscriptions"),
        )
        subscriptions_by_key = _subscription_lookup(subscriptions)
        rows = []

        for sku in subscribed_skus:
            product_sku = sku.get("productSku") or {}
            total_units = int(
                sku.get("availableUnits")
                or sku.get("totalUnits")
                or sku.get("activeUnits")
                or sku.get("quantity")
                or 0
            )
            assigned_units = int(
                sku.get("consumedUnits")
                or sku.get("assignedUnits")
                or sku.get("usedUnits")
                or 0
            )
            suspended_units = int(sku.get("suspendedUnits") or 0)
            warning_units = int(sku.get("warningUnits") or 0)
            locked_out_units = int(sku.get("lockedOutUnits") or 0)
            enabled_units = max(total_units - suspended_units - warning_units, 0)
            available_units = max(total_units - assigned_units, 0)
            sku_id = _text(
                sku.get("skuId")
                or sku.get("productSkuId")
                or product_sku.get("id")
                or product_sku.get("skuId")
            )
            sku_part_number = (
                _text(sku.get("skuPartNumber"))
                or _text(sku.get("name"))
                or _text(product_sku.get("name"))
                or sku_id
            )
            subscription_details = _subscription_details_for_partner_sku(
                sku,
                subscriptions_by_key,
            )
            lifecycle_dates = [
                detail.get("nextLifecycleDateTime")
                for detail in subscription_details
                if detail.get("nextLifecycleDateTime")
            ]
            subscription_ids = [
                detail["subscriptionId"]
                for detail in subscription_details
                if detail.get("subscriptionId")
            ]

            row = {
                "organizationId": tenant_id,
                "organizationName": customer_name,
                "tenantId": tenant_id,
                "accountName": _customer_domain(customer),
                "accountId": tenant_id,
                "appliesTo": sku.get("appliesTo") or "Company",
                "skuId": sku_id,
                "skuPartNumber": sku_part_number,
                "status": sku.get("status") or sku.get("capabilityStatus") or "Enabled",
                "totalUnits": total_units,
                "enabledUnits": enabled_units,
                "assignedUnits": assigned_units,
                "availableUnits": available_units,
                "warningUnits": warning_units,
                "suspendedUnits": suspended_units,
                "lockedOutUnits": locked_out_units,
                "subscriptionIds": subscription_ids,
                "subscriptionDetails": subscription_details,
                "subscriptionCount": len(subscription_ids),
                "nextLifecycleDateTime": min(lifecycle_dates) if lifecycle_dates else None,
                "lifecycleLookupError": None,
                "dataSource": "partnerCenter",
                "syncId": sync_id,
                "syncedAt": _now(),
            }
            rows.append(row)

        return {
            "tenantId": tenant_id,
            "customerName": customer_name,
            "defaultDomain": _customer_domain(customer),
            "licenseRows": rows,
        }


async def _upsert_batches(table_name: str, documents: list[dict]) -> None:
    for start in range(0, len(documents), 100):
        await tables.upsert_batch(table_name, documents[start : start + 100])


def _dashboard_payload(
    *,
    generated_at: str,
    tenants: list[dict],
    licenses: list[dict],
    errors: list[dict],
    warnings: list[dict],
    source: str,
    last_sync: dict | None = None,
) -> dict:
    return {
        "generatedAt": generated_at,
        "source": source,
        "lastSync": last_sync,
        "summary": {
            "mappedTenants": len(tenants),
            "tenantsSucceeded": len([t for t in tenants if not t.get("lastError")]),
            "tenantsFailed": len(errors),
            "licenseSkuRows": len(licenses),
            "subscriptionReferences": sum(
                row.get("subscriptionCount") or 0 for row in licenses
            ),
            "totalUnits": sum(row.get("totalUnits") or 0 for row in licenses),
            "enabledUnits": sum(row.get("enabledUnits") or 0 for row in licenses),
            "assignedUnits": sum(row.get("assignedUnits") or 0 for row in licenses),
            "availableUnits": sum(row.get("availableUnits") or 0 for row in licenses),
        },
        "tenants": tenants,
        "licenses": licenses,
        "errors": errors,
        "warnings": warnings,
    }


async def _stored_dashboard() -> dict:
    tenant_docs = await tables.query(TENANTS_TABLE, order_by="customerName", limit=1000)
    inventory_docs = await tables.query(
        INVENTORY_TABLE,
        order_by="organizationName",
        limit=5000,
    )
    sync_docs = await tables.query(
        SYNC_RUNS_TABLE,
        order_by="startedAt",
        order_dir="desc",
        limit=1,
    )

    tenants = [doc.data for doc in tenant_docs.documents]
    licenses = [doc.data for doc in inventory_docs.documents]
    last_sync = sync_docs.documents[0].data if sync_docs.documents else None
    errors = (last_sync or {}).get("errors", [])
    warnings = (last_sync or {}).get("warnings", [])

    licenses.sort(
        key=lambda row: (
            (row.get("organizationName") or "").casefold(),
            (row.get("skuPartNumber") or "").casefold(),
        )
    )

    return _dashboard_payload(
        generated_at=(last_sync or {}).get("finishedAt") or _now(),
        tenants=[
            {
                "organizationId": row.get("tenantId"),
                "organizationName": row.get("customerName"),
                "tenantId": row.get("tenantId"),
                "skuCount": row.get("skuCount") or 0,
                "defaultDomain": row.get("defaultDomain"),
                "relationshipStatus": row.get("relationshipStatus"),
                "lastSyncAt": row.get("lastSyncAt"),
                "lastError": row.get("lastError"),
            }
            for row in tenants
        ],
        licenses=licenses,
        errors=errors,
        warnings=warnings,
        source="storedPartnerCenterSnapshot",
        last_sync=last_sync,
    )


@workflow
async def m365_discover_gdap_customers() -> dict:
    """Discover Partner Center customers and store them as managed tenants."""

    token = await _partner_token()
    now = _now()
    async with httpx.AsyncClient(timeout=60) as client:
        customers = await _partner_collection(client, token, "/customers")

    documents = []
    for customer in customers:
        tenant_id = _customer_tenant_id(customer)
        if not tenant_id:
            continue
        documents.append(
            {
                "id": tenant_id,
                "data": {
                    "tenantId": tenant_id,
                    "customerName": _customer_name(customer),
                    "defaultDomain": _customer_domain(customer),
                    "relationshipStatus": customer.get("relationshipToPartner")
                    or customer.get("relationshipStatus")
                    or "managed",
                    "partnerCustomer": customer,
                    "discoveredAt": now,
                    "lastSeenAt": now,
                    "lastError": None,
                },
            }
        )

    if documents:
        await _upsert_batches(TENANTS_TABLE, documents)

    return {
        "generatedAt": now,
        "customersDiscovered": len(documents),
        "tenants": [doc["data"] for doc in documents],
    }


@workflow
async def m365_sync_license_inventory() -> dict:
    """Sync Partner Center customer license inventory into solution tables."""

    token = await _partner_token()
    sync_id = str(uuid4())
    started_at = _now()
    errors = []
    warnings = []
    licenses = []
    tenants = []

    async with httpx.AsyncClient(timeout=90) as client:
        customers = await _partner_collection(client, token, "/customers")
        semaphore = asyncio.Semaphore(8)
        results = await asyncio.gather(
            *[
                _fetch_partner_customer(customer, client, token, semaphore, sync_id)
                for customer in customers
            ],
            return_exceptions=True,
        )

    tenant_docs = []
    inventory_docs = []
    history_docs = []

    for customer, result in zip(customers, results):
        tenant_id = _customer_tenant_id(customer) or "unknown"
        customer_name = _customer_name(customer)
        if isinstance(result, Exception):
            error = {
                "organizationId": tenant_id,
                "organizationName": customer_name,
                "tenantId": tenant_id,
                "error": str(result),
            }
            errors.append(error)
            tenant_docs.append(
                {
                    "id": tenant_id,
                    "data": {
                        "tenantId": tenant_id,
                        "customerName": customer_name,
                        "defaultDomain": _customer_domain(customer),
                        "relationshipStatus": customer.get("relationshipToPartner")
                        or customer.get("relationshipStatus")
                        or "managed",
                        "partnerCustomer": customer,
                        "lastSyncAt": started_at,
                        "lastError": str(result),
                    },
                }
            )
            continue

        tenant_rows = result["licenseRows"]
        tenants.append(
            {
                "organizationId": result["tenantId"],
                "organizationName": result["customerName"],
                "tenantId": result["tenantId"],
                "skuCount": len(tenant_rows),
                "defaultDomain": result["defaultDomain"],
                "relationshipStatus": "managed",
                "lastSyncAt": started_at,
                "lastError": None,
            }
        )
        tenant_docs.append(
            {
                "id": result["tenantId"],
                "data": {
                    "tenantId": result["tenantId"],
                    "customerName": result["customerName"],
                    "defaultDomain": result["defaultDomain"],
                    "relationshipStatus": "managed",
                    "partnerCustomer": customer,
                    "skuCount": len(tenant_rows),
                    "lastSyncAt": started_at,
                    "lastError": None,
                },
            }
        )
        for row in tenant_rows:
            row_id = _table_id(row["tenantId"], row.get("skuId") or row.get("skuPartNumber"))
            inventory_docs.append({"id": row_id, "data": row})
            history_docs.append({"id": _table_id(sync_id, row_id), "data": row})
        licenses.extend(tenant_rows)

    if tenant_docs:
        await _upsert_batches(TENANTS_TABLE, tenant_docs)
    if inventory_docs:
        await _upsert_batches(INVENTORY_TABLE, inventory_docs)
    if history_docs:
        await _upsert_batches(HISTORY_TABLE, history_docs)

    finished_at = _now()
    last_sync = {
        "syncId": sync_id,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "status": "completedWithErrors" if errors else "success",
        "customersDiscovered": len(customers),
        "tenantsSucceeded": len(tenants),
        "tenantsFailed": len(errors),
        "licenseSkuRows": len(licenses),
        "errors": errors,
        "warnings": warnings,
    }
    payload = _dashboard_payload(
        generated_at=finished_at,
        tenants=tenants,
        licenses=licenses,
        errors=errors,
        warnings=warnings,
        source="partnerCenterSync",
        last_sync=last_sync,
    )
    await tables.upsert(SYNC_RUNS_TABLE, id=sync_id, data=last_sync)
    return payload


@workflow
async def m365_license_inventory(refresh: bool = False) -> dict:
    """Return the MSP license dashboard from stored Partner Center snapshots."""

    if refresh:
        return await m365_sync_license_inventory()

    stored = await _stored_dashboard()
    if stored["licenses"] or stored["tenants"] or stored["lastSync"]:
        return stored

    return await m365_sync_license_inventory()
