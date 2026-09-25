# Security Group References

This second app in the existing Bifrost solution maps security groups to direct references in Entra and Intune. Search a group name such as `EntraID_Join_Access`, select it, and inspect the policy or assignment, inclusion/exclusion, app intent, policy state, filter ID and exact Graph field that references the group.

The original licensing app, workflows, tables and Partner Center connection retain their identities. Install/update the whole solution; deploying only the new manifest entries as a replacement would omit the licensing resources.

## Set up the Graph connection

1. Keep the existing **Microsoft Partner Center** connection. It discovers customers for both apps; its token is never used for Graph calls.
2. Use a multitenant Entra app registration in the partner tenant for the new delegated Graph connection. Set its web redirect URI to the callback URL displayed by your Bifrost OAuth connection. Configure its client ID and client secret in Bifrost, not in source or environment files committed to Git.
3. Add the **delegated** Microsoft Graph permissions below and arrange consent in each customer tenant being audited. An entry in Partner Center does not prove that Graph access or a GDAP relationship is active.
4. Configure the solution connection named exactly **Microsoft Graph Group References**. Set its entity ID to the **partner tenant ID**. The manifest supplies the authorization/token URLs and scopes. Sign in with the partner user that belongs to the security groups assigned the required customer GDAP roles. Complete MFA.
5. Open **Security Group References**, click **Discover customers**, select one customer, and click **Scan customer**. Expand incomplete coverage checks to see the failed endpoint and HTTP status. Search for a group by name or object ID and export its evidence if needed.

The workflow retrieves Bifrost's current partner refresh token and redeems it against the selected customer's token endpoint with the Graph `.default` scope. Bifrost manages the parent OAuth connection and renewal; this workflow neither stores nor returns tokens. It does not grant consent, change GDAP relationships, or modify Microsoft resources. Token acquisition is the only Microsoft POST; resource reads use GET.

This app + user approach follows Microsoft's [GDAP and secure application model](https://learn.microsoft.com/en-us/partner-center/developer/gdap-and-secure-application-model). Customer consent and the user's delegated roles are separate requirements. Configure only the customer roles needed for your chosen checks; a broad Graph scope alone does not override Intune RBAC or role-scope restrictions.

| Delegated scope | Checks |
| --- | --- |
| `Directory.Read.All` | Security groups, direct parents, enterprise application assignments and group licensing metadata |
| `Policy.Read.All` | Entra join/registration/device administrators, Conditional Access, MDM/MAM mobility scopes |
| `RoleManagement.Read.Directory` | Active directory roles and PIM assignment/eligibility schedule instances |
| `DeviceManagementConfiguration.Read.All` | Configuration, Settings Catalog, endpoint security, compliance and related assignments |
| `DeviceManagementApps.Read.All` | Intune applications, app configuration/protection and policy sets |
| `DeviceManagementServiceConfig.Read.All` | Enrollment, Autopilot and update-related service configuration |
| `DeviceManagementRBAC.Read.All` | Intune role assignments and scope tag assignments |
| `DeviceManagementScripts.Read.All` | PowerShell/shell platform scripts and remediations |
| `CloudPC.Read.All` | Windows 365 provisioning and user settings |
| `offline_access` | Delegated refresh token |

The template requests all scopes in this table. To omit an optional permission such as Cloud PC, edit the connection's consent scopes; its checks will still run and show incomplete if access is denied. Do not add write scopes merely to make a coverage indicator green. The [current script API](https://learn.microsoft.com/en-us/graph/api/intune-devices-devicemanagementscript-list?view=graph-rest-beta) documents `DeviceManagementScripts.Read.All`. See also the permission requirements for [device registration policy](https://learn.microsoft.com/en-us/graph/api/deviceregistrationpolicy-get?view=graph-rest-1.0) and [group application assignments](https://learn.microsoft.com/en-us/graph/api/group-list-approleassignments?view=graph-rest-1.0).

## Coverage and interpretation

The [collector catalog](../functions/group_references.py) contains the complete endpoint list. The dashboard reports 35 checks:

- Direct parent groups, administrative units and directory roles; enterprise application role assignments; assigned license SKU IDs.
- Entra device join, device registration and device administrator settings; automatic MDM enrollment and MAM mobility scopes; Conditional Access including disabled/report-only policies.
- Directory role assignments and PIM active/eligible schedule instances, with directory scope and role definition evidence.
- Intune enrollment restrictions/ESP/Windows Hello, configuration profiles, Settings Catalog/modern endpoint security, administrative templates, compliance and legacy security intents.
- Intune app assignments, app configuration, app protection, policy sets, platform/remediation scripts, Autopilot, Windows update profiles/policies, Windows 365, scope tags and administrator role assignments.

**No matches means no direct references found within the completed checks. It never means unused or safe to delete.** This product does not make a deletion recommendation. A failed check marks coverage incomplete even if other checks find useful evidence. Coverage is deliberately conservative across the tenant: a relationship read failing for one group makes that category incomplete for all groups in the snapshot.

Every collection follows pagination, including each policy's assignments. References read before a later page fails are retained. References are exact group identifiers in supported structured fields; names and free text are not treated as evidence. All Users/All Devices targeting and assignment filters may affect members independently of a group's direct references. Policy state and filter identifiers are shown without calculating effective applicability. License names currently appear as SKU IDs.

Not scanned: Azure RBAC, SharePoint ACLs, Exchange workload permissions, Teams policies, Power Platform, external applications, entitlement management, access reviews, dynamic membership expressions, transitive/effective access, or historical Microsoft references. Many Intune and mobility endpoints use `/beta`; endpoint availability and licensing vary. Only the Microsoft public cloud is supported.

## Persistence and operations

| Resource | Purpose |
| --- | --- |
| `group_reference_discover` | Refresh the separate customer catalog from Partner Center |
| `group_reference_scan(tenant_id)` | Scan one discovered customer and publish a snapshot |
| `group_reference_inventory(tenant_id)` | Read customers, scan progress, coverage and group summaries |
| `group_reference_details(tenant_id, group_id, snapshot_id)` | Read a group's evidence from the displayed snapshot |
| `group_reference_state` | Customer catalog, tenant progress/snapshot pointer and tenant locks |
| `group_reference_groups` | Group summaries keyed by snapshot and group |
| `group_reference_findings` | Evidence rows keyed by snapshot and evidence hash |

Scans run on demand, one selected customer at a time, with a 30-minute workflow timeout. Group relationship requests use at most six concurrent requests; all resource reads have timeouts, bounded retries and pagination-loop detection. Repeated scans can be scheduled per tenant using the existing workflow. There is no automatic all-customer scan on page load.

A tenant lock is inserted atomically to prevent overlapping scans across browser sessions. Progress is saved between checks and every 24 groups during relationship scans. The UI polls progress while running. If a Bifrost worker is killed before cleanup, the lock remains: first confirm the execution has stopped in Bifrost, then delete **only** the `lock:<tenant UUID>` document in `group_reference_state` before retrying. A long-running status with an old start time can indicate an interrupted execution; do not remove an active execution's lock.

All new rows are written before the tenant's snapshot pointer changes. Authentication, group inventory, or storage failures retain the previous published snapshot and show a failed/latest-scan banner. Category-level failures publish the evidence obtained, with incomplete coverage. Old snapshots remain stored so an already-open detail view stays consistent during publication; they are **not automatically purged**. Budget storage accordingly and apply a retention process to inactive snapshots when needed. Never remove a snapshot still referenced by a tenant's `snapshotId`.

This is an **internal MSP dashboard**. It follows the existing solution's authenticated app/workflow access and broad solution table policies. It is not a customer portal and does not isolate individual customers from internal users. Restrict solution access to the intended staff before use; the customer selector is navigation, not an authorization boundary. No tokens, script bodies, group member lists or raw OAuth error payloads are persisted in the snapshot tables.

## Development and validation

To create a ZIP for Bifrost's Install Solution dialog, commit the intended source and run `python scripts/package_solution.py /path/to/solution-install.zip` from the repository. The package must contain `bifrost.solution.yaml` and `.bifrost/` at the **archive root**, without an enclosing repository folder. GitHub's source ZIP and `git archive --prefix` packages are not directly installable. An incorrectly nested archive can appear as an empty package and trigger a misleading password error in the installer. This shareable package is not encrypted and requires no backup password.

Python tests use fake Bifrost storage and real `httpx` with a mock transport:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Use your Bifrost instance's CLI to install/update its runtime SDK for the new app, then run:

```sh
bifrost solution sdk update . --app group-reference-dashboard
cd apps/group-reference-dashboard
npm install
npm run typecheck
npm run build
```

The SDK is supplied by Bifrost at deployment and is intentionally absent from npm dependencies. The local declaration file describes only the consumed API. During development without an instance, the app was built against the public Bifrost SDK source, not a live tenant connection. Run an initial customer scan in your instance to validate actual consent, roles, endpoint visibility and the deployed SDK version.

The UI fixture at `tests/preview.html` is for local development only (`npm run dev`, then open `/tests/preview.html`). It contains synthetic customers and reference examples; it is not part of the production entry. Query parameters `state=empty`, `state=running`, `state=failed`, `state=loading`, `state=denied`, and `theme=dark` exercise visual states.
