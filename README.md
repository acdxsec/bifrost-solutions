# Microsoft 365 License Dashboard

This Bifrost Solution includes an MSP Microsoft 365 license dashboard and a **Security Group References** dashboard.

Both dashboards use Partner Center customer discovery instead of one Bifrost organization per customer tenant. Licensing uses the existing Partner Center integration. Group References adds a separate Microsoft Graph delegated connection and requires customer consent and GDAP roles.

See [Group References setup, coverage and operation](docs/group-references.md) for the new dashboard. The licensing setup below is unchanged; its statements about consent apply only to Partner Center licensing reads.

## What It Installs

In addition to the licensing resources below, version 0.2.0 installs the Security Group References app, four `group_reference_*` workflows, three snapshot/state tables and a Microsoft Graph connection template. Existing resource IDs and the solution slug are preserved.

- `m365_discover_gdap_customers`
  - Reads Partner Center customers.
  - Stores them in `m365_managed_tenants`.
- `m365_sync_license_inventory`
  - Reads each customer tenant's subscribed SKUs through Partner Center.
  - Stores current rows in `m365_license_inventory`.
  - Stores point-in-time rows in `m365_license_history`.
  - Stores sync status/errors in `m365_license_sync_runs`.
- `m365_license_inventory`
  - Returns the dashboard payload from stored tables.
  - Runs a first sync automatically when no snapshot exists.
- `Microsoft 365 License Dashboard`
  - Standalone v2 dashboard app.
  - Reads stored inventory by default.
  - The Sync button runs a fresh Partner Center sync.

## Required Integration

The solution uses an integration named exactly:

`Microsoft Partner Center`

This is intentionally separate from any `Microsoft 365 Graph` integration. The Partner Center token is issued by the MSP partner tenant; customer tenant IDs are only used as Partner Center customer IDs in API paths.

Configure the integration with:

- Entity ID / default entity ID: the MSP partner tenant ID.
- Authorization URL: `https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/authorize`
- Token URL: `https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token`
- Delegated scopes:
  - `https://api.partnercenter.microsoft.com/user_impersonation`
  - `offline_access`

## Microsoft Access Model

This package assumes:

- The MSP has Partner Center access.
- Customer tenants are managed through GDAP / partner relationships.
- The MSP identity has the Partner Center and customer roles needed to read customer subscription/license data.
- The dashboard does not require a separate customer admin-consent flow for each tenant.
- The workflow authenticates once to the MSP partner tenant, then calls customer-scoped Partner Center endpoints.

The workflow calls:

- `GET https://api.partnercenter.microsoft.com/v1/customers`
- `GET https://api.partnercenter.microsoft.com/v1/customers/{customer_id}/subscribedskus`
- `GET https://api.partnercenter.microsoft.com/v1/customers/{customer_id}/subscriptions`

## Data Model

The solution declares these tables:

- `m365_managed_tenants`
- `m365_license_inventory`
- `m365_license_history`
- `m365_license_sync_runs`

The dashboard reads from `m365_license_inventory` and the latest `m365_license_sync_runs` row. It does not live-query every customer on every page load.

## Lifecycle Dates

Partner Center subscription rows may include commitment or renewal dates. The sync maps those into `nextLifecycleDateTime` when returned.

If Partner Center does not return a lifecycle date for a SKU/subscription, the dashboard shows `Not returned`.
