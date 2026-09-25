"""Direct security-group references; all Microsoft resource requests are GETs.

Snapshots are published only after every row is stored. Coverage is part of the
result: denied, failed and unimplemented checks never mean a group is unused.
"""
import asyncio
import hashlib
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx
from bifrost import integrations, tables, workflow

GRAPH = "https://graph.microsoft.com"
STATE = "group_reference_state"
GROUPS = "group_reference_groups"
REFS = "group_reference_findings"
GRAPH_INTEGRATION = "Microsoft Graph Group References"
LIMITATIONS = [
    "Direct references visible to the connected account only; no unused-group verdict.",
    "Azure RBAC, SharePoint permissions, Exchange workload permissions, Teams policies, Power Platform and external applications are not scanned.",
    "Nested/effective access, dynamic membership expressions, entitlement management, access reviews and historical references are not evaluated.",
    "All Users / All Devices targeting can affect members without referencing their group. Assignment filters and disabled policies are reported, not evaluated.",
]

# label, Graph path, child collection, read full detail, singleton
CATALOG = [
    ("Entra join, registration and device administrators", "/v1.0/policies/deviceRegistrationPolicy", "", False, True),
    ("Automatic MDM enrollment", "/beta/policies/mobileDeviceManagementPolicies", "includedGroups", False, False),
    ("MAM mobility scopes", "/beta/policies/mobileAppManagementPolicies", "includedGroups", False, False),
    ("Conditional Access", "/v1.0/identity/conditionalAccess/policies", "", False, False),
    *[(f"Entra {kind}", f"/v1.0/roleManagement/directory/{kind}?$expand=roleDefinition", "", False, False)
      for kind in ("roleAssignments", "roleEligibilityScheduleInstances", "roleAssignmentScheduleInstances")],
    *[(label, f"/beta/{path}", "assignments", False, False) for label, path in [
        ("Enrollment restrictions, ESP and Windows Hello", "deviceManagement/deviceEnrollmentConfigurations"),
        ("Device configuration", "deviceManagement/deviceConfigurations"),
        ("Settings Catalog and endpoint security", "deviceManagement/configurationPolicies"),
        ("Administrative Templates", "deviceManagement/groupPolicyConfigurations"),
        ("Compliance", "deviceManagement/deviceCompliancePolicies"),
        ("Legacy endpoint security", "deviceManagement/intents"),
        ("Applications", "deviceAppManagement/mobileApps"),
        ("Managed-device app configuration", "deviceAppManagement/mobileAppConfigurations"),
        ("Managed-app configuration", "deviceAppManagement/targetedManagedAppConfigurations"),
        ("Android app protection", "deviceAppManagement/androidManagedAppProtections"),
        ("iOS app protection", "deviceAppManagement/iosManagedAppProtections"),
        ("Windows app protection", "deviceAppManagement/windowsManagedAppProtections"),
        ("PowerShell platform scripts", "deviceManagement/deviceManagementScripts"),
        ("Shell platform scripts", "deviceManagement/deviceShellScripts"),
        ("Remediation scripts", "deviceManagement/deviceHealthScripts"),
        ("Autopilot deployment", "deviceManagement/windowsAutopilotDeploymentProfiles"),
        ("Policy sets", "deviceAppManagement/policySets"),
        ("Windows feature updates", "deviceManagement/windowsFeatureUpdateProfiles"),
        ("Windows quality update profiles", "deviceManagement/windowsQualityUpdateProfiles"),
        ("Windows quality update policies", "deviceManagement/windowsQualityUpdatePolicies"),
        ("Windows driver updates", "deviceManagement/windowsDriverUpdateProfiles"),
        ("Windows 365 provisioning", "deviceManagement/virtualEndpoint/provisioningPolicies"),
        ("Windows 365 user settings", "deviceManagement/virtualEndpoint/userSettings"),
        ("Intune scope tags", "deviceManagement/roleScopeTags"),
    ]],
    ("Intune administrator role assignments", "/beta/deviceManagement/roleAssignments", "", True, False),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def guid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


class AuditError(RuntimeError):
    """Only explicitly sanitized messages may cross the workflow boundary."""


def safe_error(exc):
    return str(exc) if isinstance(exc, AuditError) else "Request or storage operation failed. Inspect the Bifrost execution log."


class Reader:
    def __init__(self, client, token, base=GRAPH):
        self.client, self.token, self.base = client, token, base

    async def get(self, path):
        url = path if path.startswith("https://") else self.base + path
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != urlsplit(self.base).netloc or parsed.username:
            raise AuditError("Unexpected pagination host; request blocked.")
        for attempt in range(4):
            try:
                response = await self.client.get(url, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
            except httpx.TransportError:
                if attempt == 3:
                    raise AuditError("Network timeout or connection failure.") from None
                await asyncio.sleep(2 ** attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                try:
                    delay = float(response.headers.get("Retry-After", 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                if delay > 60:
                    raise AuditError(f"HTTP {response.status_code}: throttled; retry the scan later.")
                await asyncio.sleep(max(0, delay))
                continue
            if response.status_code != 200:
                hint = " Check consent and delegated roles." if response.status_code in (401, 403) else ""
                raise AuditError(f"HTTP {response.status_code}.{hint}")
            try:
                payload = response.json()
            except ValueError:
                raise AuditError("Invalid JSON response.") from None
            if not isinstance(payload, dict):
                raise AuditError("Unexpected response shape.")
            return payload
        raise AuditError("Request retries exhausted.")

    async def items(self, path, partner=False):
        seen = set()
        while path:
            if path in seen or len(seen) >= 10000:
                raise AuditError("Pagination did not complete; scan is incomplete.")
            seen.add(path)
            payload = await self.get(path)
            rows = payload.get("items" if partner else "value")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise AuditError("Expected an object collection; scan is incomplete.")
            for row in rows:
                yield row
            path = payload.get("@odata.nextLink")
            if partner and not path:
                link = (payload.get("links") or {}).get("next")
                path = link.get("uri") if isinstance(link, dict) else link
            if path and not isinstance(path, str):
                raise AuditError("Invalid pagination link.")


async def customer_token(client, tenant_id):
    integration = await integrations.get(GRAPH_INTEGRATION)
    oauth = integration.oauth if integration else None
    if not oauth or not oauth.client_id or not oauth.client_secret or not oauth.refresh_token:
        raise AuditError(f"Connect the '{GRAPH_INTEGRATION}' delegated OAuth integration first.")
    try:
        response = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={"grant_type": "refresh_token", "client_id": oauth.client_id,
                  "client_secret": oauth.client_secret, "refresh_token": oauth.refresh_token,
                  "scope": "https://graph.microsoft.com/.default"},
        )
        payload = response.json()
    except (httpx.TransportError, ValueError):
        raise AuditError("Microsoft Graph token exchange failed; check the connection and retry.") from None
    if response.status_code != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        raise AuditError(f"Customer Graph sign-in failed (HTTP {response.status_code}). Check customer app consent, GDAP roles and the partner OAuth connection.")
    return payload["access_token"]


# Match identifiers only in reference-bearing fields, never descriptions or names.
REFERENCE_FIELDS = {"groupid", "entraobjectid", "principalid", "includegroups", "excludegroups", "groups", "members", "scopemembers", "resourcescopes"}


def matches(value, group_ids, path="", active=False):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from matches(child, group_ids, f"{path}.{key}".strip("."), key.lower() in REFERENCE_FIELDS)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from matches(child, group_ids, f"{path}[{index}]", active)
    elif active and (group_id := guid(value)) in group_ids:
        yield group_id, path


def finding(group_id, category, obj, payload, field, source):
    target = payload.get("target") or {}
    kind = str(target.get("@odata.type", ""))
    mode = "Exclude" if "exclusion" in kind.lower() or "exclude" in field.lower() else "Include" if "groupassignmenttarget" in kind.lower() or "include" in field.lower() else "Reference"
    return {
        "groupId": group_id, "category": category,
        "resourceName": obj.get("displayName") or obj.get("name") or obj.get("resourceDisplayName") or (obj.get("roleDefinition") or {}).get("displayName") or obj.get("id") or category,
        "resourceId": obj.get("id", ""), "field": field, "mode": mode,
        "source": source, "assignmentId": payload.get("id", ""),
        "intent": payload.get("intent", ""), "policyState": obj.get("state", ""),
        "filterId": target.get("deviceAndAppManagementAssignmentFilterId", ""),
        "filterType": target.get("deviceAndAppManagementAssignmentFilterType", ""),
        "roleDefinitionId": obj.get("roleDefinitionId", ""),
        "directoryScopeId": obj.get("directoryScopeId", ""),
        "appRoleId": obj.get("appRoleId", ""),
    }


class Scanner:
    def __init__(self, reader, groups, progress=None):
        self.reader = reader
        self.groups = groups
        self.ids = {g["id"] for g in groups}
        self.findings = []
        self.coverage = []
        self.progress = progress

    def add(self, category, obj, payload, source, explicit=None, prefix=""):
        hits = [(explicit, prefix)] if explicit else matches(payload, self.ids)
        for group_id, field in hits:
            self.findings.append(finding(group_id, category, obj, payload, field, source))

    async def check(self, label, operation):
        check = {"category": label, "status": "complete", "examined": 0, "errorCount": 0, "errors": []}
        self.coverage.append(check)
        def fail(exc, source):
            check["status"] = "incomplete"
            check["errorCount"] += 1
            if len(check["errors"]) < 10:
                check["errors"].append({"source": source, "message": safe_error(exc)})
        try:
            await operation(check, fail)
        except Exception as exc:
            fail(exc, label)

    async def category(self, spec):
        label, path, child, detail, singleton = spec
        async def run(check, fail):
            async def objects():
                if singleton:
                    yield await self.reader.get(path)
                else:
                    async for obj in self.reader.items(path):
                        yield obj
            async for obj in objects():
                check["examined"] += 1
                source = path.split("?")[0] + "/" + quote(str(obj.get("id", "")), safe="")
                try:
                    if detail:
                        obj = await self.reader.get(source)
                    if not child:
                        self.add(label, obj, obj, path if singleton else source)
                    else:
                        async for assignment in self.reader.items(source + "/" + child):
                            if child == "includedGroups":
                                gid = guid(assignment.get("id"))
                                if gid in self.ids:
                                    self.add(label, obj, assignment, source + "/" + child, gid, "includedGroups")
                            else:
                                self.add(label, obj, assignment, source + "/" + child)
                except Exception as exc:
                    fail(exc, source)
        await self.check(label, run)

    async def relationships(self, child, label):
        async def run(check, fail):
            semaphore = asyncio.Semaphore(6)
            async def one(group):
                path = f"/v1.0/groups/{group['id']}/{child}"
                try:
                    async with semaphore:
                        async for obj in self.reader.items(path):
                            check["examined"] += 1
                            self.add(label, obj, obj, path, group["id"], child)
                except Exception as exc:
                    fail(exc, path)
            for start in range(0, len(self.groups), 24):
                await asyncio.gather(*(one(group) for group in self.groups[start:start + 24]))
                if self.progress:
                    await self.progress(f"{label}: {min(start + 24, len(self.groups))}/{len(self.groups)} groups")
        await self.check(label, run)

    def licenses(self):
        for group in self.groups:
            for sku in group.get("assignedLicenses", []):
                self.add("Group-based licensing", {"id": sku["skuId"], "displayName": "License SKU " + sku["skuId"]}, {}, f"/v1.0/groups/{group['id']}", group["id"], "assignedLicenses")
        self.coverage.append({"category": "Group-based licensing", "status": "complete", "examined": len(self.groups), "errorCount": 0, "errors": []})


async def rows(table, where=None):
    cursor = ""
    while True:
        page = await tables.query(table, where=where, limit=500, after_document_id=cursor)
        if not page.documents:
            return
        for doc in page.documents:
            yield doc
        next_cursor = page.documents[-1].id
        if cursor == next_cursor:
            raise AuditError("Stored snapshot pagination did not complete.")
        cursor = next_cursor


async def write_batches(table, documents):
    for start in range(0, len(documents), 100):
        await tables.upsert_batch(table, documents[start:start + 100])


@workflow
async def group_reference_discover() -> dict:
    """Discover customers without altering the licensing dashboard snapshots."""
    try:
        integration = await integrations.get("Microsoft Partner Center")
        if not integration or not integration.oauth or not integration.oauth.access_token:
            raise AuditError("Connect Microsoft Partner Center first.")
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            reader = Reader(client, integration.oauth.access_token, "https://api.partnercenter.microsoft.com")
            tenants = []
            async for customer in reader.items("/v1/customers?size=500", partner=True):
                profile = customer.get("companyProfile") or {}
                tid = guid(customer.get("id") or profile.get("tenantId"))
                if not tid:
                    raise AuditError("Customer discovery returned an invalid tenant ID.")
                tenants.append({"tenantId": tid, "name": profile.get("companyName") or customer.get("companyName") or tid})
        tenants = list({t["tenantId"]: t for t in tenants}.values())
        await tables.upsert(STATE, "catalog", {"tenants": tenants, "discoveredAt": now()})
        return {"count": len(tenants)}
    except Exception as exc:
        raise AuditError(safe_error(exc)) from None


async def catalog():
    doc = await tables.get(STATE, "catalog")
    return doc.data if doc else {"tenants": []}


@workflow
async def group_reference_scan(tenant_id: str) -> dict:
    """Scan one discovered tenant; run separately per tenant to bound execution."""
    tenant_id = guid(tenant_id)
    tenant = next((t for t in (await catalog())["tenants"] if t["tenantId"] == tenant_id), None)
    if not tenant:
        raise AuditError("Choose a customer from Discover customers first.")
    lock_id = "lock:" + tenant_id
    try:
        await tables.insert(STATE, {"startedAt": now(), "tenantId": tenant_id}, id=lock_id)
    except Exception:
        # Atomic insert prevents two browser sessions from publishing out of order.
        raise AuditError("Scan could not start. Another scan may hold the tenant lock; check Bifrost executions and table access.") from None
    previous = None
    previous_loaded = False
    state = {"tenantId": tenant_id, "name": tenant["name"], "status": "running", "startedAt": now(), "progress": "Connecting to Microsoft Graph", "completedChecks": 0, "totalChecks": len(CATALOG) + 3}
    try:
        previous = await tables.get(STATE, tenant_id)
        previous_loaded = True
        if previous:
            state.update({k: v for k, v in previous.data.items() if k in ("snapshotId", "scannedAt", "coverage", "groupCount", "referenceCount")})
        await tables.upsert(STATE, tenant_id, state)
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            reader = Reader(client, await customer_token(client, tenant_id))
            groups = []
            async for group in reader.items("/v1.0/groups?$filter=securityEnabled%20eq%20true&$select=id,displayName,description,groupTypes,onPremisesSyncEnabled,assignedLicenses&$top=999"):
                if not guid(group.get("id")) or not isinstance(group.get("assignedLicenses"), list):
                    raise AuditError("Group inventory is missing required fields; snapshot was not replaced.")
                group["id"] = guid(group["id"])
                groups.append(group)
            async def progress(message):
                state["progress"] = message
                await tables.upsert(STATE, tenant_id, state)
            scanner = Scanner(reader, groups, progress)
            scanner.licenses()
            for child, label in (("memberOf", "Parent groups, administrative units and directory roles"), ("appRoleAssignments", "Enterprise application assignments")):
                state["progress"] = label
                await tables.upsert(STATE, tenant_id, state)
                await scanner.relationships(child, label)
                state["completedChecks"] = len(scanner.coverage)
            for spec in CATALOG:
                state["progress"] = spec[0]
                await tables.upsert(STATE, tenant_id, state)
                await scanner.category(spec)
                state["completedChecks"] = len(scanner.coverage)
        snapshot = str(uuid4())
        timestamp = now()
        counts = {g["id"]: 0 for g in groups}
        documents = {}
        for item in scanner.findings:
            # One evidence row per group, assignment and matching field.
            key = hashlib.sha256("|".join(str(item[k]) for k in ("groupId", "category", "resourceId", "assignmentId", "field")).encode()).hexdigest()
            documents[key] = {"id": snapshot + ":" + key, "data": {**item, "snapshotId": snapshot, "tenantId": tenant_id}}
        for doc in documents.values():
            counts[doc["data"]["groupId"]] += 1
        complete = all(c["status"] == "complete" for c in scanner.coverage)
        await write_batches(REFS, list(documents.values()))
        await write_batches(GROUPS, [{"id": snapshot + ":" + g["id"], "data": {
            "snapshotId": snapshot, "tenantId": tenant_id, "groupId": g["id"],
            "name": g.get("displayName") or g["id"], "description": g.get("description") or "",
            "membership": "Dynamic" if "DynamicMembership" in g.get("groupTypes", []) else "Assigned",
            "onPremises": bool(g.get("onPremisesSyncEnabled")), "referenceCount": counts[g["id"]],
        }} for g in groups])
        state.update({"snapshotId": snapshot, "scannedAt": timestamp, "finishedAt": timestamp, "status": "complete" if complete else "incomplete", "progress": "Scan finished", "coverage": scanner.coverage, "groupCount": len(groups), "referenceCount": len(documents)})
        # Publication is a single atomic pointer replacement after durable writes.
        await tables.upsert(STATE, tenant_id, state)
        return {"status": state["status"], "groups": len(groups), "references": len(documents)}
    except Exception as exc:
        # Keep the prior published snapshot, even if a new snapshot write failed.
        failed = dict(previous.data) if previous else {"tenantId": tenant_id, "name": tenant["name"]}
        failed.update({"status": "failed", "startedAt": state["startedAt"], "finishedAt": now(), "progress": safe_error(exc)})
        if previous_loaded:
            await tables.upsert(STATE, tenant_id, failed)
        raise AuditError(safe_error(exc)) from None
    finally:
        await tables.delete_batch(STATE, [lock_id])


@workflow
async def group_reference_inventory(tenant_id: str = "") -> dict:
    """Read cached summaries. Browser requests never return credentials."""
    result = {**await catalog(), "limitations": LIMITATIONS, "tenant": None, "groups": []}
    if tenant_id:
        doc = await tables.get(STATE, guid(tenant_id) or "invalid")
        if doc:
            result["tenant"] = doc.data
            if doc.data.get("status") == "running" and not await tables.get(STATE, "lock:" + guid(tenant_id)):
                result["tenant"] = {**doc.data, "status": "failed", "progress": "The previous execution stopped before publishing. Retry the scan."}
            snapshot = doc.data.get("snapshotId")
            if snapshot:
                result["groups"] = [d.data async for d in rows(GROUPS, {"snapshotId": snapshot})]
    return result


@workflow
async def group_reference_details(tenant_id: str, group_id: str, snapshot_id: str) -> dict:
    """Bind detail to the displayed snapshot, including during a new scan."""
    tenant_id, group_id, snapshot_id = guid(tenant_id), guid(group_id), guid(snapshot_id)
    if not all((tenant_id, group_id, snapshot_id)):
        raise AuditError("Invalid group or snapshot identifier.")
    group = await tables.get(GROUPS, snapshot_id + ":" + group_id)
    if not group or group.data.get("tenantId") != tenant_id:
        raise AuditError("The group is not in this tenant snapshot. Refresh the dashboard.")
    return {"tenantId": tenant_id, "groupId": group_id, "snapshotId": snapshot_id,
            "references": [d.data async for d in rows(REFS, {"snapshotId": snapshot_id, "groupId": group_id, "tenantId": tenant_id})]}
