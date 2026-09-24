# Microsoft 365 License Dashboard

This Bifrost Solution contains:

- A provider-scoped workflow that enumerates every organization mapping on the existing `Microsoft 365 Graph` integration.
- A standalone Bifrost v2 dashboard app that calls that workflow and displays Microsoft 365 subscribed SKU inventory across all mapped tenants.

## Required existing integration

The Bifrost instance must already contain an integration named exactly:

`Microsoft 365 Graph`

Each Bifrost customer organization to appear in the dashboard needs an Integration Mapping whose External Entity is that customer's Microsoft Entra tenant ID.

## Required Microsoft Graph application permission

The Entra application used by the `Microsoft 365 Graph` integration needs these **Application** permissions:

- `LicenseAssignment.Read.All`
- `Directory.Read.All`

Grant admin consent in each tenant that Bifrost will query.

The workflow calls:

- `GET https://graph.microsoft.com/v1.0/subscribedSkus`
- `GET https://graph.microsoft.com/beta/directory/subscriptions`

The beta subscriptions endpoint supplies `nextLifecycleDateTime`. Microsoft Graph beta APIs are not production-stable, and `nextLifecycleDateTime` is a lifecycle transition date rather than a guaranteed contract expiration date.

## Dashboard data

The dashboard shows:

- Bifrost organization / Microsoft tenant
- SKU part number and SKU ID
- total, enabled, assigned, and available units
- warning, suspended, and locked-out units
- capability status
- Microsoft Graph `subscriptionIds`
- Microsoft Graph beta `nextLifecycleDateTime` when available
- tenant-specific errors without failing the entire dashboard

Microsoft Graph reports quantities at the `subscribedSku` level. If a SKU references multiple subscription IDs, Graph does not provide an exact quantity split per individual subscription ID through this endpoint.
