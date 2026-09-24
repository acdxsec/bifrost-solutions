# Microsoft 365 License Dashboard

This Bifrost Solution is an MSP-oriented Microsoft 365 license dashboard.

It uses one MSP Microsoft integration and Partner Center / GDAP customer access instead of one Bifrost organization per customer tenant.

## What It Installs

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

## Required Existing Integration

The Bifrost instance must already contain an integration named exactly:

`Microsoft 365 Graph`

That integration must authenticate the MSP/partner tenant identity that can call Partner Center APIs for GDAP-managed customers.

The workflow requests a Partner Center token with:

`https://api.partnercenter.microsoft.com/.default`

## Microsoft Access Model

This package assumes:

- The MSP has Partner Center access.
- Customer tenants are managed through GDAP / partner relationships.
- The MSP identity has the Partner Center and customer roles needed to read customer subscription/license data.
- The dashboard does not require a separate customer admin-consent flow for each tenant.

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
