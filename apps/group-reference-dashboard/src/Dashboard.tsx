import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  CheckCircle2,
  CircleHelp,
  Layers3,
  RefreshCw,
  Search,
  ShieldCheck,
  Unplug,
} from "lucide-react";
import type { Group, Inventory, Reference } from "./types";

const time = (value?: string) =>
  value ? new Date(value).toLocaleString() : "Not scanned";
const csvCell = (value: unknown) => {
  let text = String(value ?? "");
  if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
  return `"${text.replace(/"/g, '""')}"`;
};
function download(rows: unknown[][], name: string) {
  const url = URL.createObjectURL(
    new Blob(
      ["\ufeff" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n")],
      { type: "text/csv;charset=utf-8" },
    ),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ReferenceList({ references }: { references: Reference[] }) {
  if (!references.length)
    return (
      <div className="empty">
        <CircleHelp />
        <h3>No direct references found</h3>
        <p>
          Review scan coverage below. This is not evidence that the group is
          unused.
        </p>
      </div>
    );
  return (
    <>
      <div className="section-heading">
        <span>
          {references.length} reference{references.length === 1 ? "" : "s"}{" "}
          found
        </span>
        <button
          className="text-button"
          onClick={() =>
            download(
              [
                [
                  "Group ID",
                  "Category",
                  "Resource",
                  "Resource ID",
                  "Mode",
                  "Intent",
                  "Policy state",
                  "Matched field",
                  "Filter ID",
                  "Filter type",
                  "Source",
                ],
                ...references.map((r) => [
                  r.groupId,
                  r.category,
                  r.resourceName,
                  r.resourceId,
                  r.mode,
                  r.intent,
                  r.policyState,
                  r.field,
                  r.filterId,
                  r.filterType,
                  r.source,
                ]),
              ],
              "group-references.csv",
            )
          }
        >
          <ArrowDownToLine size={15} />
          Export evidence
        </button>
      </div>
      <div className="references">
        {references.map((r, i) => (
          <article
            className="reference"
            key={`${r.category}:${r.assignmentId}:${r.field}:${i}`}
          >
            <div className="reference-top">
              <span className="eyebrow">{r.category}</span>
              <span
                className={`badge ${r.mode === "Exclude" ? "amber" : "teal"}`}
              >
                {r.mode}
              </span>
            </div>
            <h3>{r.resourceName}</h3>
            <p className="mono muted">{r.resourceId}</p>
            <div className="chips">
              {r.intent && <span>Intent: {r.intent}</span>}
              {r.policyState && <span>Policy: {r.policyState}</span>}
              {r.filterId && (
                <span>Filter: {r.filterType || "configured"}</span>
              )}
            </div>
            <details>
              <summary>Reference evidence</summary>
              <dl>
                <dt>Matched field</dt>
                <dd className="mono">{r.field}</dd>
                {r.assignmentId && (
                  <>
                    <dt>Assignment ID</dt>
                    <dd className="mono">{r.assignmentId}</dd>
                  </>
                )}
                {r.filterId && (
                  <>
                    <dt>Filter ID</dt>
                    <dd className="mono">{r.filterId}</dd>
                  </>
                )}
                {r.roleDefinitionId && (
                  <>
                    <dt>Role definition</dt>
                    <dd className="mono">{r.roleDefinitionId}</dd>
                  </>
                )}
                {r.directoryScopeId && (
                  <>
                    <dt>Directory scope</dt>
                    <dd className="mono">{r.directoryScopeId}</dd>
                  </>
                )}
                {r.appRoleId && (
                  <>
                    <dt>Application role</dt>
                    <dd className="mono">{r.appRoleId}</dd>
                  </>
                )}
                <dt>Graph endpoint</dt>
                <dd className="mono">{r.source}</dd>
              </dl>
            </details>
          </article>
        ))}
      </div>
    </>
  );
}

type Props = {
  data: Inventory | null;
  tenantId: string;
  onTenant: (id: string) => void;
  busy: boolean;
  loading: boolean;
  error: string;
  onDiscover: () => void;
  onScan: () => void;
  onRefresh: () => void;
  renderDetails: (group: Group) => ReactNode;
};

export default function Dashboard({
  data,
  tenantId,
  onTenant,
  busy,
  loading,
  error,
  onDiscover,
  onScan,
  onRefresh,
  renderDetails,
}: Props) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState("");
  useEffect(() => {
    setSelected("");
    setSearch("");
    setFilter("all");
  }, [tenantId]);
  // The SDK retains the previous successful result during a parameter change.
  const tenant = data?.tenant?.tenantId === tenantId ? data.tenant : null;
  const groups = tenant ? (data?.groups ?? []) : [];
  const checks = tenant?.coverage ?? [];
  const incomplete = checks.filter((c) => c.status !== "complete");
  const running = tenant?.status === "running";
  const stale = tenant?.status === "failed" || running;
  const visible = useMemo(
    () =>
      groups
        .filter(
          (g) =>
            (g.name + " " + g.groupId)
              .toLowerCase()
              .includes(search.toLowerCase()) &&
            (filter === "all" ||
              (filter === "referenced"
                ? g.referenceCount > 0
                : g.referenceCount === 0)),
        )
        .sort((a, b) => a.name.localeCompare(b.name)),
    [groups, search, filter],
  );
  const current = visible.find((g) => g.groupId === selected) ?? visible[0];
  const hasSnapshot = Boolean(tenant?.snapshotId);
  const referenced = groups.filter((g) => g.referenceCount > 0).length;
  return (
    <main>
      <div className="hero">
        <div>
          <span className="eyebrow">MICROSOFT 365 · DIRECTORY VISIBILITY</span>
          <h1>Security group references</h1>
          <p>
            See where a group is used, and which checks still need attention.
          </p>
        </div>
        <div className="hero-icon">
          <Layers3 size={30} />
        </div>
      </div>
      <section className="toolbar" aria-label="Customer and scan controls">
        <label>
          Customer
          <select
            value={tenantId}
            disabled={busy}
            onChange={(e) => onTenant(e.target.value)}
          >
            <option value="">Select a customer</option>
            {data?.tenants.map((t) => (
              <option value={t.tenantId} key={t.tenantId}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <div className="toolbar-actions">
          <button onClick={onDiscover} disabled={busy}>
            Discover customers
          </button>
          <button
            onClick={onRefresh}
            disabled={loading || busy}
            aria-label="Refresh saved results"
          >
            <RefreshCw size={16} />
          </button>
          <button
            className="primary"
            onClick={onScan}
            disabled={!tenantId || busy || running}
          >
            <ShieldCheck size={16} />
            {running || busy ? "Working…" : "Scan customer"}
          </button>
        </div>
      </section>
      {error && (
        <div role="alert" className="notice warning">
          {error}
        </div>
      )}
      {running && (
        <div className="notice" role="status">
          <RefreshCw size={17} className="spin" />
          <div>
            <strong>Scanning {tenant?.name}</strong>
            <p>
              {tenant?.progress} · {tenant?.completedChecks ?? 0} /{" "}
              {tenant?.totalChecks ?? 0} checks complete
            </p>
            <small>
              Started {time(tenant?.startedAt)}. Saved results below remain from
              the previous scan.
            </small>
          </div>
        </div>
      )}
      {tenant?.status === "failed" && (
        <div className="notice warning" role="alert">
          <Unplug size={19} />
          <div>
            <strong>Latest scan failed</strong>
            <p>{tenant.progress}</p>
            <small>
              {hasSnapshot
                ? `Showing the previous snapshot from ${time(tenant.scannedAt)}.`
                : "No snapshot has been saved."}
            </small>
          </div>
        </div>
      )}
      <div className="metrics">
        <div>
          <span>Security groups</span>
          <strong>{hasSnapshot ? groups.length : "—"}</strong>
          <small>In the saved snapshot</small>
        </div>
        <div>
          <span>With references</span>
          <strong>{hasSnapshot ? referenced : "—"}</strong>
          <small>
            {hasSnapshot
              ? `${tenant?.referenceCount ?? 0} evidence rows`
              : "Direct assignments and settings"}
          </small>
        </div>
        <div>
          <span>No matches found</span>
          <strong>{hasSnapshot ? groups.length - referenced : "—"}</strong>
          <small>Within the completed checks</small>
        </div>
        <div className={incomplete.length ? "attention" : ""}>
          <span>Scan coverage</span>
          <strong>
            {hasSnapshot
              ? `${checks.length - incomplete.length}/${checks.length}`
              : "—"}
          </strong>
          <small>
            {incomplete.length
              ? `${incomplete.length} checks need attention`
              : "Supported checks completed"}
          </small>
        </div>
      </div>
      <div className="snapshot-line">
        <span>
          <span
            className={`dot ${stale || incomplete.length ? "amber-dot" : ""}`}
          />
          Snapshot: {time(tenant?.scannedAt)}
        </span>
        <span>Direct references · Microsoft public cloud</span>
      </div>
      {!hasSnapshot ? (
        <section className="panel empty">
          <Layers3 size={32} />
          <h2>
            {loading
              ? "Loading saved results…"
              : data?.tenants.length
                ? "Ready to map your groups"
                : "Connect your customer directory"}
          </h2>
          <p>
            {data?.tenants.length
              ? "Choose a customer and run a scan. Group names, references and coverage will appear here."
              : "Configure the Partner Center and Microsoft Graph integrations, then discover your customers."}
          </p>
        </section>
      ) : (
        <>
          {incomplete.length > 0 && (
            <div className="notice warning">
              <CircleHelp size={18} />
              <p>
                <strong>Coverage is incomplete.</strong> Found references are
                useful evidence. Groups with no matches still require review of
                the failed checks.
              </p>
            </div>
          )}
          <div className="workspace">
            <section className="panel group-panel" aria-label="Security groups">
              <div className="panel-heading">
                <h2>Groups</h2>
                <span className="count">{visible.length}</span>
              </div>
              <div className="filters">
                <label className="search">
                  <Search size={16} />
                  <input
                    aria-label="Search groups"
                    placeholder="Search name or object ID"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
                <select
                  aria-label="Filter references"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                >
                  <option value="all">All groups</option>
                  <option value="referenced">With references</option>
                  <option value="none">No matches found</option>
                </select>
              </div>
              <div className="group-list">
                {visible.map((g) => (
                  <button
                    className={`group-row ${g.groupId === current?.groupId ? "selected" : ""}`}
                    key={g.groupId}
                    aria-pressed={g.groupId === current?.groupId}
                    onClick={() => setSelected(g.groupId)}
                  >
                    <div>
                      <strong>{g.name}</strong>
                      <small>
                        {g.membership} ·{" "}
                        {g.onPremises ? "Synced from AD" : "Cloud"}
                      </small>
                    </div>
                    <span
                      className={`count ${!g.referenceCount ? "zero" : ""}`}
                    >
                      {g.referenceCount}
                    </span>
                  </button>
                ))}
                {!visible.length && (
                  <p className="empty">No groups match these filters.</p>
                )}
              </div>
            </section>
            <section
              className="panel detail-panel"
              aria-label="Selected group references"
            >
              {current ? (
                <>
                  <div className="detail-heading">
                    <span className="eyebrow">SELECTED GROUP</span>
                    <h2>{current.name}</h2>
                    <p className="mono muted">{current.groupId}</p>
                    {current.description && <p>{current.description}</p>}
                    <a
                      href={`https://entra.microsoft.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Overview/groupId/${encodeURIComponent(current.groupId)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open group in Entra <ArrowUpRight size={14} />
                    </a>
                    <small className="muted">
                      Use the {tenant?.name} directory in Entra.
                    </small>
                  </div>
                  {renderDetails(current)}
                </>
              ) : (
                <div className="empty">
                  <h3>Select a group</h3>
                  <p>Its reference evidence will appear here.</p>
                </div>
              )}
            </section>
          </div>
          <section className="panel coverage">
            <div className="panel-heading">
              <div>
                <h2>Scan coverage</h2>
                <p>
                  Completed means all requested pages were read with the
                  connected account’s permissions.
                </p>
              </div>
              <span className="badge">{checks.length} checks</span>
            </div>
            <div className="coverage-list">
              {checks.map((c) => (
                <details key={c.category}>
                  <summary>
                    <span
                      className={
                        c.status === "complete" ? "teal-text" : "amber-text"
                      }
                    >
                      {c.status === "complete" ? (
                        <CheckCircle2 size={16} />
                      ) : (
                        <CircleHelp size={16} />
                      )}
                    </span>
                    <span>{c.category}</span>
                    <span className="coverage-result">
                      {c.status === "complete"
                        ? `${c.examined} examined`
                        : `${c.errorCount} failed requests`}
                    </span>
                  </summary>
                  {c.errors.length ? (
                    c.errors.map((e, i) => (
                      <div className="check-error" key={i}>
                        <p>{e.message}</p>
                        <code>{e.source}</code>
                      </div>
                    ))
                  ) : (
                    <p className="check-error">
                      Completed within the supported scope. No request failures
                      recorded.
                    </p>
                  )}
                  {c.errorCount > c.errors.length && (
                    <p className="check-error">
                      Showing the first {c.errors.length} failures.
                    </p>
                  )}
                </details>
              ))}
            </div>
          </section>
        </>
      )}
      <footer>
        <h3>What this scan can tell you</h3>
        {(
          data?.limitations ?? [
            "Direct references only. A group with no matches is not necessarily unused.",
          ]
        ).map((line) => (
          <p key={line}>{line}</p>
        ))}
      </footer>
    </main>
  );
}
