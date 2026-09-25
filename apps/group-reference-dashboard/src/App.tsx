import { useEffect, useState } from "react";
import { BifrostHeader, useWorkflowMutation, useWorkflowQuery } from "bifrost";
import Dashboard from "./Dashboard";
import type { Details, Inventory } from "./types";

const ref = (name: string) =>
  `functions/group_references.py::group_reference_${name}`;

function GroupDetails({
  tenantId,
  groupId,
  snapshotId,
}: {
  tenantId: string;
  groupId: string;
  snapshotId: string;
}) {
  const query = useWorkflowQuery<Details>(ref("details"), {
    tenant_id: tenantId,
    group_id: groupId,
    snapshot_id: snapshotId,
  });
  if (query.error)
    return (
      <p role="alert" className="notice warning">
        Could not load references. Refresh the dashboard or inspect the Bifrost
        execution.
      </p>
    );
  if (!query.data || query.loading)
    return (
      <p className="empty" role="status">
        Loading reference evidence…
      </p>
    );
  return <ReferenceList references={query.data.references} />;
}
import { ReferenceList } from "./Dashboard";

export default function App() {
  const [tenantId, setTenantId] = useState("");
  const [actionError, setActionError] = useState("");
  const query = useWorkflowQuery<Inventory>(ref("inventory"), {
    tenant_id: tenantId,
  });
  const scan = useWorkflowMutation(ref("scan"));
  const discover = useWorkflowMutation(ref("discover"));
  const busy = scan.loading || discover.loading;
  const refresh = query.refresh;

  useEffect(() => {
    if (!tenantId && query.data?.tenants.length)
      setTenantId(query.data.tenants[0].tenantId);
  }, [tenantId, query.data]);

  useEffect(() => {
    if (!scan.loading && query.data?.tenant?.status !== "running") return;
    // No overlapping poll requests, even on a slow Bifrost execution service.
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh();
      } catch {
        /* Query error is rendered below. */
      }
      if (!stopped) timer = setTimeout(poll, 5000);
    };
    timer = setTimeout(poll, 1500);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [scan.loading, query.data?.tenant?.status, refresh]);

  async function act(kind: "scan" | "discover" | "refresh") {
    setActionError("");
    try {
      if (kind === "scan") await scan.mutate({ tenant_id: tenantId });
      if (kind === "discover") await discover.mutate();
      await refresh();
    } catch {
      setActionError(
        kind === "scan"
          ? "Scan did not finish. The last saved results remain available; see scan status or the Bifrost execution for details."
          : "Request failed. Check the Bifrost connection and execution details, then retry.",
      );
      if (kind !== "refresh") {
        try {
          await refresh();
        } catch {
          /* Keep the action error. */
        }
      }
    }
  }

  return (
    <>
      <BifrostHeader title="Security Group References" />
      <Dashboard
        data={query.data}
        tenantId={tenantId}
        onTenant={setTenantId}
        busy={busy}
        loading={query.loading}
        error={
          actionError ||
          (query.error
            ? "Could not read saved results. Check Bifrost table access and refresh."
            : "")
        }
        onDiscover={() => void act("discover")}
        onScan={() => void act("scan")}
        onRefresh={() => void act("refresh")}
        renderDetails={(group) =>
          query.data?.tenant?.snapshotId ? (
            <GroupDetails
              key={`${tenantId}:${query.data.tenant.snapshotId}:${group.groupId}`}
              tenantId={tenantId}
              groupId={group.groupId}
              snapshotId={query.data.tenant.snapshotId}
            />
          ) : null
        }
      />
    </>
  );
}
