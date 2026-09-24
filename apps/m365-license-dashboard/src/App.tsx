import { useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  RefreshCw,
  Search,
  ShieldAlert,
} from "lucide-react";
import { BifrostHeader, useWorkflowQuery } from "bifrost";

type LicenseRow = {
  organizationId: string;
  organizationName: string;
  tenantId: string;
  accountName: string | null;
  accountId: string | null;
  appliesTo: string | null;
  skuId: string | null;
  skuPartNumber: string | null;
  status: string | null;
  totalUnits: number;
  enabledUnits: number;
  assignedUnits: number;
  availableUnits: number;
  warningUnits: number;
  suspendedUnits: number;
  lockedOutUnits: number;
  subscriptionIds: string[];
  subscriptionDetails: Array<{
    subscriptionId: string;
    id?: string | null;
    commerceSubscriptionId?: string | null;
    ocpSubscriptionId?: string | null;
    createdDateTime?: string | null;
    nextLifecycleDateTime?: string | null;
    status?: string | null;
    totalLicenses?: number | null;
    isTrial?: boolean | null;
  }>;
  subscriptionCount: number;
  nextLifecycleDateTime: string | null;
  lifecycleLookupError: string | null;
};

type TenantError = {
  organizationId: string;
  organizationName: string;
  tenantId: string;
  error: string;
};

type TenantWarning = {
  organizationId: string;
  organizationName: string;
  tenantId: string;
  warning: string;
};

type InventoryResult = {
  generatedAt: string;
  summary: {
    mappedTenants: number;
    tenantsSucceeded: number;
    tenantsFailed: number;
    licenseSkuRows: number;
    subscriptionReferences: number;
    totalUnits: number;
    enabledUnits: number;
    assignedUnits: number;
    availableUnits: number;
  };
  tenants: Array<{
    organizationId: string;
    organizationName: string;
    tenantId: string;
    skuCount: number;
  }>;
  licenses: LicenseRow[];
  errors: TenantError[];
  warnings: TenantWarning[];
};

const WORKFLOW_REF =
  "functions/license_inventory.py::m365_license_inventory";

function number(value: number) {
  return new Intl.NumberFormat().format(value);
}

function percent(assigned: number, total: number) {
  if (!total) return "—";
  return `${Math.round((assigned / total) * 100)}%`;
}

function date(value: string | null) {
  if (!value) return null;

  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

function statusClass(status: string | null) {
  const normalized = (status ?? "").toLowerCase();

  if (normalized === "enabled") return "status status-good";
  if (normalized === "warning") return "status status-warn";
  if (normalized === "suspended") return "status status-bad";

  return "status";
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: number;
  detail?: string;
}) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{number(value)}</div>
      {detail ? <div className="metric-detail">{detail}</div> : null}
    </div>
  );
}

export default function App() {
  const inventory = useWorkflowQuery<InventoryResult>(WORKFLOW_REF);
  const [search, setSearch] = useState("");
  const [tenant, setTenant] = useState("all");
  const [status, setStatus] = useState("all");
  const warnings = inventory.data?.warnings ?? [];

  const tenantOptions = useMemo(() => {
    const names = new Set(
      (inventory.data?.licenses ?? []).map(
        (row) => row.organizationName,
      ),
    );

    return Array.from(names).sort((a, b) =>
      a.localeCompare(b),
    );
  }, [inventory.data]);

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();

    return (inventory.data?.licenses ?? []).filter((row) => {
      if (
        tenant !== "all" &&
        row.organizationName !== tenant
      ) {
        return false;
      }

      if (
        status !== "all" &&
        (row.status ?? "").toLowerCase() !== status
      ) {
        return false;
      }

      if (!term) return true;

      const haystack = [
        row.organizationName,
        row.tenantId,
        row.skuPartNumber ?? "",
        row.skuId ?? "",
        row.accountName ?? "",
        row.nextLifecycleDateTime ?? "",
        ...row.subscriptionIds,
      ]
        .join(" ")
        .toLowerCase();

      return haystack.includes(term);
    });
  }, [inventory.data, search, tenant, status]);

  return (
    <div className="app-shell">
      <BifrostHeader title="Microsoft 365 License Dashboard" />

      <main className="dashboard">
        <section className="page-heading">
          <div>
            <div className="eyebrow">
              Microsoft 365
            </div>
            <h1>License Inventory</h1>
            <p>
              Live subscribed-SKU inventory across every organization
              mapped to the Microsoft 365 Graph integration.
            </p>
          </div>

          <button
            className="primary-button"
            onClick={() => void inventory.refetch()}
            disabled={inventory.isLoading}
          >
            <RefreshCw
              size={16}
              className={inventory.isLoading ? "spin" : ""}
            />
            {inventory.isLoading ? "Refreshing" : "Refresh"}
          </button>
        </section>

        {inventory.isLoading && !inventory.data ? (
          <section className="state-card">
            <RefreshCw size={22} className="spin" />
            <div>
              <strong>Loading Microsoft 365 licenses</strong>
              <span>Querying mapped tenants through Microsoft Graph.</span>
            </div>
          </section>
        ) : null}

        {inventory.isError ? (
          <section className="state-card state-error">
            <AlertCircle size={22} />
            <div>
              <strong>Inventory workflow failed</strong>
              <span>
                {inventory.errorMessage ?? "Unknown workflow error"}
              </span>
            </div>
          </section>
        ) : null}

        {inventory.data ? (
          <>
            <section className="metrics">
              <Metric
                label="Mapped tenants"
                value={inventory.data.summary.mappedTenants}
                detail={`${inventory.data.summary.tenantsSucceeded} queried successfully`}
              />
              <Metric
                label="Purchased units"
                value={inventory.data.summary.totalUnits}
                detail={`${inventory.data.summary.licenseSkuRows} subscribed SKU rows`}
              />
              <Metric
                label="Assigned units"
                value={inventory.data.summary.assignedUnits}
                detail={`${percent(
                  inventory.data.summary.assignedUnits,
                  inventory.data.summary.totalUnits,
                )} utilization`}
              />
              <Metric
                label="Available units"
                value={inventory.data.summary.availableUnits}
                detail={`${number(
                  inventory.data.summary.subscriptionReferences,
                )} subscription references`}
              />
              <Metric
                label="Tenant failures"
                value={inventory.data.summary.tenantsFailed}
                detail={
                  inventory.data.summary.tenantsFailed
                    ? "Review errors below"
                    : "All mapped tenants responded"
                }
              />
            </section>

            <section className="panel">
              <div className="panel-toolbar">
                <div className="search-field">
                  <Search size={16} />
                  <input
                    value={search}
                    onChange={(event) =>
                      setSearch(event.target.value)
                    }
                    placeholder="Search tenant, SKU, tenant ID, or subscription ID"
                  />
                </div>

                <select
                  value={tenant}
                  onChange={(event) =>
                    setTenant(event.target.value)
                  }
                >
                  <option value="all">All tenants</option>
                  {tenantOptions.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>

                <select
                  value={status}
                  onChange={(event) =>
                    setStatus(event.target.value)
                  }
                >
                  <option value="all">All statuses</option>
                  <option value="enabled">Enabled</option>
                  <option value="warning">Warning</option>
                  <option value="suspended">Suspended</option>
                  <option value="lockedout">Locked out</option>
                </select>
              </div>

              <div className="table-summary">
                <span>
                  Showing <strong>{number(rows.length)}</strong> license
                  SKU rows
                </span>
                <span>
                  Updated{" "}
                  {new Date(
                    inventory.data.generatedAt,
                  ).toLocaleString()}
                </span>
              </div>

              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Organization</th>
                      <th>License SKU</th>
                      <th>Status</th>
                      <th className="numeric">Purchased</th>
                      <th className="numeric">Assigned</th>
                      <th className="numeric">Available</th>
                      <th className="numeric">Use</th>
                      <th>Next lifecycle</th>
                      <th>Subscriptions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr
                        key={`${row.tenantId}:${row.skuId}:${row.accountId ?? ""}`}
                      >
                        <td>
                          <div className="primary-cell">
                            {row.organizationName}
                          </div>
                          <div className="secondary-cell mono">
                            {row.tenantId}
                          </div>
                        </td>
                        <td>
                          <div className="primary-cell">
                            {row.skuPartNumber || "Unknown SKU"}
                          </div>
                          <div className="secondary-cell mono">
                            {row.skuId}
                          </div>
                        </td>
                        <td>
                          <span className={statusClass(row.status)}>
                            {row.status || "Unknown"}
                          </span>
                          {row.warningUnits ||
                          row.suspendedUnits ||
                          row.lockedOutUnits ? (
                            <div className="unit-state-detail">
                              {row.warningUnits
                                ? `${row.warningUnits} warning `
                                : ""}
                              {row.suspendedUnits
                                ? `${row.suspendedUnits} suspended `
                                : ""}
                              {row.lockedOutUnits
                                ? `${row.lockedOutUnits} locked`
                                : ""}
                            </div>
                          ) : null}
                        </td>
                        <td className="numeric">
                          {number(row.totalUnits)}
                        </td>
                        <td className="numeric">
                          {number(row.assignedUnits)}
                        </td>
                        <td className="numeric">
                          {number(row.availableUnits)}
                        </td>
                        <td className="numeric">
                          {percent(
                            row.assignedUnits,
                            row.totalUnits,
                          )}
                        </td>
                        <td>
                          {row.lifecycleLookupError ? (
                            <span className="secondary-cell">
                              Unavailable
                            </span>
                          ) : (row.subscriptionDetails ?? []).some(
                              (subscription) =>
                                subscription.nextLifecycleDateTime,
                            ) ? (
                            <div className="lifecycle-list">
                              {(row.subscriptionDetails ?? []).map(
                                (subscription) => (
                                  <div
                                    className="lifecycle-item"
                                    key={subscription.subscriptionId}
                                  >
                                    <span>
                                      {date(
                                        subscription.nextLifecycleDateTime ??
                                          null,
                                      ) ?? "Not returned"}
                                    </span>
                                    {subscription.status ? (
                                      <span className="secondary-cell">
                                        {subscription.status}
                                      </span>
                                    ) : null}
                                  </div>
                                ),
                              )}
                            </div>
                          ) : (
                            <span className="secondary-cell">
                              Not returned
                            </span>
                          )}
                        </td>
                        <td>
                          {row.subscriptionIds.length ? (
                            <div className="subscription-list">
                              {row.subscriptionIds.map((id) => (
                                <span
                                  className="subscription-id mono"
                                  key={id}
                                >
                                  {id}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <span className="secondary-cell">
                              No subscription ID returned
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {!rows.length ? (
                  <div className="empty-state">
                    No license rows match the current filters.
                  </div>
                ) : null}
              </div>
            </section>

            {inventory.data.errors.length ? (
              <section className="panel error-panel">
                <div className="error-heading">
                  <ShieldAlert size={18} />
                  <div>
                    <strong>Tenant query failures</strong>
                    <span>
                      These mappings were retained in the dashboard but
                      Microsoft Graph could not be queried.
                    </span>
                  </div>
                </div>

                <div className="error-list">
                  {inventory.data.errors.map((error) => (
                    <div
                      className="error-row"
                      key={`${error.organizationId}:${error.tenantId}`}
                    >
                      <div>
                        <strong>{error.organizationName}</strong>
                        <span className="mono">
                          {error.tenantId}
                        </span>
                      </div>
                      <span>{error.error}</span>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}

            {warnings.length ? (
              <section className="panel warning-panel">
                <div className="warning-heading">
                  <ShieldAlert size={18} />
                  <div>
                    <strong>Lifecycle lookup warnings</strong>
                    <span>
                      License counts loaded, but Microsoft Graph beta
                      lifecycle data was unavailable for these tenants.
                    </span>
                  </div>
                </div>

                <div className="error-list">
                  {warnings.map((warning) => (
                    <div
                      className="error-row"
                      key={`${warning.organizationId}:${warning.tenantId}`}
                    >
                      <div>
                        <strong>{warning.organizationName}</strong>
                        <span className="mono">
                          {warning.tenantId}
                        </span>
                      </div>
                      <span>{warning.warning}</span>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}

            {!inventory.data.errors.length &&
            !warnings.length ? (
              <section className="success-strip">
                <CheckCircle2 size={17} />
                All mapped tenants queried successfully.
              </section>
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  );
}
