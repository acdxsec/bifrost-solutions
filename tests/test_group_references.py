"""Offline contract tests: python -m unittest discover -s tests -v."""
import asyncio
import copy
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
# Decorators/platform persistence are injected; HTTP requests use real httpx
# against a MockTransport so pagination and credential boundaries are exercised.
sys.modules.setdefault("bifrost", types.SimpleNamespace(integrations=None, tables=None, workflow=lambda f: f))
spec = importlib.util.spec_from_file_location("audit", ROOT / "functions/group_references.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

TENANT = "11111111-1111-4111-8111-111111111111"
GROUP = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
OLD = "44444444-4444-4444-8444-444444444444"


def group():
    return {"id": GROUP, "displayName": "EntraID_Join_Access", "assignedLicenses": [], "groupTypes": []}


class MemoryTables:
    def __init__(self):
        self.data = {audit.STATE: {"catalog": {"tenants": [{"tenantId": TENANT, "name": "Example customer"}]}}, audit.GROUPS: {}, audit.REFS: {}}
        self.fail_on = None

    async def get(self, table, doc_id):
        value = self.data[table].get(doc_id)
        return types.SimpleNamespace(id=doc_id, data=copy.deepcopy(value)) if value is not None else None

    async def insert(self, table, data, id):
        if id in self.data[table]:
            raise RuntimeError("conflict")
        await self.upsert(table, id, data)

    async def upsert(self, table, id, data):
        if table == self.fail_on:
            raise RuntimeError("simulated storage failure with sensitive detail")
        self.data[table][id] = copy.deepcopy(data)

    async def upsert_batch(self, table, documents):
        for doc in documents:
            await self.upsert(table, doc["id"], doc["data"])

    async def delete_batch(self, table, ids):
        for id in ids:
            self.data[table].pop(id, None)

    async def query(self, table, where=None, after_document_id="", limit=500):
        docs = [types.SimpleNamespace(id=k, data=copy.deepcopy(v)) for k, v in sorted(self.data[table].items())
                if k > after_document_id and all(v.get(key) == value for key, value in (where or {}).items())]
        return types.SimpleNamespace(documents=docs[:min(limit, 2)])


class MatchTests(unittest.TestCase):
    def test_exact_fields_only_no_name_or_substring_matches(self):
        obj = {"id": GROUP, "displayName": GROUP, "description": GROUP,
               "conditions": {"users": {"includeGroups": [GROUP.upper()], "excludeGroups": [OTHER]}},
               "target": {"groupId": "prefix-" + GROUP}}
        self.assertEqual(list(audit.matches(obj, {GROUP})), [(GROUP, "conditions.users.includeGroups[0]")])

    def test_join_policy_group_reference(self):
        obj = {"azureADJoin": {"allowedToJoin": {"@odata.type": "#microsoft.graph.enumeratedDeviceRegistrationMembership", "groups": [GROUP]}}}
        self.assertEqual(list(audit.matches(obj, {GROUP})), [(GROUP, "azureADJoin.allowedToJoin.groups[0]")])

    def test_exclusion_filter_and_app_intent_preserved(self):
        value = audit.finding(GROUP, "Apps", {"id": "app", "displayName": "App"},
                              {"intent": "uninstall", "target": {"@odata.type": "#microsoft.graph.exclusionGroupAssignmentTarget", "deviceAndAppManagementAssignmentFilterId": "filter", "deviceAndAppManagementAssignmentFilterType": "exclude"}}, "target.groupId", "/test")
        self.assertEqual((value["mode"], value["intent"], value["filterId"]), ("Exclude", "uninstall", "filter"))


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_and_empty_collection(self):
        seen = []
        def handler(req):
            seen.append(req.url.path)
            return httpx.Response(200, json={"value": [{"id": "one"}], "@odata.nextLink": audit.GRAPH + "/second"} if len(seen) == 1 else {"value": []})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = [x async for x in audit.Reader(client, "secret").items("/first")]
        self.assertEqual(result, [{"id": "one"}])
        self.assertEqual(seen, ["/first", "/second"])

    async def test_cross_host_pagination_never_receives_bearer(self):
        seen = []
        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(200, json={"value": [], "@odata.nextLink": "https://unexpected.invalid/steal"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(audit.AuditError, "Unexpected pagination host"):
                _ = [x async for x in audit.Reader(client, "secret").items("/first")]
        self.assertEqual(len(seen), 1)

    async def test_repeated_page_is_incomplete(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"value": [], "@odata.nextLink": "/loop"}))) as client:
            with self.assertRaisesRegex(audit.AuditError, "Pagination did not complete"):
                _ = [x async for x in audit.Reader(client, "secret").items("/loop")]

    async def test_retry_and_redaction(self):
        responses = [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"value": []})]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: responses.pop(0))) as client:
            self.assertEqual([x async for x in audit.Reader(client, "secret").items("/first")], [])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(403, json={"error": "secret-token"}))) as client:
            with self.assertRaises(audit.AuditError) as raised:
                await audit.Reader(client, "secret").get("/first")
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    async def test_malformed_page_never_reports_empty_success(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}))) as client:
            with self.assertRaises(audit.AuditError):
                _ = [x async for x in audit.Reader(client, "secret").items("/first")]

    async def test_refresh_exchange_uses_customer_tenant_graph_audience(self):
        oauth = types.SimpleNamespace(client_id="client", client_secret="secret", refresh_token="refresh")
        integration = types.SimpleNamespace(get=AsyncMock(return_value=types.SimpleNamespace(oauth=oauth)))
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={"access_token": "access"})
        with patch.object(audit, "integrations", integration):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                self.assertEqual(await audit.customer_token(client, TENANT), "access")
        self.assertEqual(requests[0].url.path, f"/{TENANT}/oauth2/v2.0/token")
        self.assertIn(b"https%3A%2F%2Fgraph.microsoft.com%2F.default", requests[0].content)

    async def test_partial_assignment_pages_preserve_evidence(self):
        def handler(req):
            if req.url.path == "/beta/policies":
                return httpx.Response(200, json={"value": [{"id": "p", "displayName": "Example"}]})
            if req.url.path.endswith("/assignments"):
                return httpx.Response(200, json={"value": [{"id": "a", "target": {"groupId": GROUP}}], "@odata.nextLink": audit.GRAPH + "/denied"})
            return httpx.Response(403)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            scan = audit.Scanner(audit.Reader(client, "secret"), [group()])
            await scan.category(("Example", "/beta/policies", "assignments", False, False))
        self.assertEqual(len(scan.findings), 1)
        self.assertEqual(scan.coverage[0]["status"], "incomplete")


class FakeReader:
    def __init__(self, *_): pass
    async def items(self, path):
        if path.startswith("/v1.0/groups?"):
            yield group()
    async def get(self, path):
        return {"id": "join", "azureADJoin": {"allowedToJoin": {"groups": [GROUP]}}}


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tables = MemoryTables()
        self.patches = [patch.object(audit, "tables", self.tables), patch.object(audit, "Reader", FakeReader), patch.object(audit, "customer_token", AsyncMock(return_value="token"))]
        for p in self.patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    async def test_publish_group_and_reference_then_read_same_snapshot(self):
        result = await audit.group_reference_scan(TENANT)
        self.assertEqual(result["status"], "complete")
        view = await audit.group_reference_inventory(TENANT)
        self.assertEqual(view["groups"][0]["name"], "EntraID_Join_Access")
        self.assertEqual(view["groups"][0]["referenceCount"], 1)
        details = await audit.group_reference_details(TENANT, GROUP, view["tenant"]["snapshotId"])
        self.assertEqual(details["references"][0]["field"], "azureADJoin.allowedToJoin.groups[0]")
        self.assertNotIn("lock:" + TENANT, self.tables.data[audit.STATE])

    async def test_failed_write_keeps_old_snapshot_and_releases_lock(self):
        self.tables.data[audit.STATE][TENANT] = {"tenantId": TENANT, "snapshotId": OLD, "scannedAt": "old"}
        self.tables.fail_on = audit.GROUPS
        with self.assertRaises(audit.AuditError):
            await audit.group_reference_scan(TENANT)
        saved = self.tables.data[audit.STATE][TENANT]
        self.assertEqual(saved["snapshotId"], OLD)
        self.assertEqual(saved["status"], "failed")
        self.assertNotIn("sensitive", saved["progress"])
        self.assertNotIn("lock:" + TENANT, self.tables.data[audit.STATE])

    async def test_group_inventory_failure_does_not_publish_empty(self):
        self.tables.data[audit.STATE][TENANT] = {"tenantId": TENANT, "snapshotId": OLD}
        class Denied(FakeReader):
            async def items(self, path):
                raise audit.AuditError("HTTP 403.")
                yield  # async generator
        with patch.object(audit, "Reader", Denied), self.assertRaises(audit.AuditError):
            await audit.group_reference_scan(TENANT)
        self.assertEqual(self.tables.data[audit.STATE][TENANT]["snapshotId"], OLD)

    async def test_unknown_tenant_cannot_scan(self):
        with self.assertRaisesRegex(audit.AuditError, "Discover"):
            await audit.group_reference_scan(OTHER)

    async def test_interrupted_scan_without_lock_is_retryable(self):
        self.tables.data[audit.STATE][TENANT] = {"tenantId": TENANT, "status": "running", "snapshotId": OLD}
        view = await audit.group_reference_inventory(TENANT)
        self.assertEqual(view["tenant"]["status"], "failed")
        self.assertEqual(view["tenant"]["snapshotId"], OLD)

    async def test_lock_blocks_duplicate_scan_without_clearing_owner_lock(self):
        self.tables.data[audit.STATE]["lock:" + TENANT] = {"owner": "other"}
        with self.assertRaises(audit.AuditError):
            await audit.group_reference_scan(TENANT)
        self.assertEqual(self.tables.data[audit.STATE]["lock:" + TENANT], {"owner": "other"})

    async def test_cross_tenant_snapshot_details_rejected(self):
        self.tables.data[audit.GROUPS][OLD + ":" + GROUP] = {"tenantId": TENANT}
        with self.assertRaises(audit.AuditError):
            await audit.group_reference_details(OTHER, GROUP, OLD)

    async def test_table_pagination_reads_all_rows(self):
        for i in range(7):
            self.tables.data[audit.GROUPS][str(i)] = {"tenantId": TENANT}
        self.assertEqual(len([d async for d in audit.rows(audit.GROUPS)]), 7)

    async def test_new_scan_hides_removed_references(self):
        await audit.group_reference_scan(TENANT)
        class NoReferences(FakeReader):
            async def get(self, path): return {"id": "join"}
        with patch.object(audit, "Reader", NoReferences):
            await audit.group_reference_scan(TENANT)
        view = await audit.group_reference_inventory(TENANT)
        self.assertEqual(view["groups"][0]["referenceCount"], 0)
        details = await audit.group_reference_details(TENANT, GROUP, view["tenant"]["snapshotId"])
        self.assertEqual(details["references"], [])


if __name__ == "__main__": unittest.main()
