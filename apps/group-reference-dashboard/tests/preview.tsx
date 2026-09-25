import { useState } from "react";
import { createRoot } from "react-dom/client";
import Dashboard, { ReferenceList } from "../src/Dashboard";
import type { Inventory, Reference } from "../src/types";
import "../src/index.css";

const params = new URLSearchParams(location.search);
if (params.get("theme") === "dark")
  document.documentElement.classList.add("dark");
const tid = "11111111-1111-4111-8111-111111111111";
const gid = "22222222-2222-4222-8222-222222222222";
const reference: Reference = {
  groupId: gid,
  category: "Entra join, registration and device administrators",
  resourceName: "Device registration policy",
  resourceId: "deviceRegistrationPolicy",
  field: "azureADJoin.allowedToJoin.groups[0]",
  mode: "Reference",
  source: "/v1.0/policies/deviceRegistrationPolicy",
  assignmentId: "",
  intent: "",
  policyState: "",
  filterId: "",
  filterType: "",
  roleDefinitionId: "",
  directoryScopeId: "",
  appRoleId: "",
};
const data: Inventory = {
  tenants: [{ tenantId: tid, name: "Northwind Services (sample)" }],
  tenant: {
    tenantId: tid,
    name: "Northwind Services (sample)",
    status: "incomplete",
    snapshotId: "sample",
    scannedAt: "2026-09-25T14:30:00Z",
    progress: "Scan finished",
    referenceCount: 19,
    groupCount: 8,
    coverage: [
      ...[
        "Group-based licensing",
        "Parent groups, administrative units and directory roles",
        "Enterprise application assignments",
        "Entra join, registration and device administrators",
        "Automatic MDM enrollment",
        "Conditional Access",
        "Device configuration",
        "Settings Catalog and endpoint security",
        "Compliance",
        "Applications",
      ].map((category, i) => ({
        category,
        status: "complete",
        examined: i * 3 + 1,
        errorCount: 0,
        errors: [],
      })),
      {
        category: "Windows 365 provisioning",
        status: "incomplete",
        examined: 0,
        errorCount: 1,
        errors: [
          {
            source:
              "/beta/deviceManagement/virtualEndpoint/provisioningPolicies",
            message: "HTTP 403. Check consent and delegated roles.",
          },
        ],
      },
    ],
  },
  groups: [
    "EntraID_Join_Access",
    "Intune-Config-AADJoin",
    "Intune-Config-BitLocker",
    "Intune-Config-Defender",
    "Intune-Config-EnhancedHardwareInventory",
    "Intune-Config-WindowsUpdate",
    "Intune-Users-Enrollment",
    "ZP-Test-Intune-Computer",
  ].map((name, i) => ({
    name,
    groupId:
      i === 0 ? gid : `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
    description:
      i === 0 ? "Users permitted to join devices to Microsoft Entra ID." : "",
    referenceCount: [2, 0, 4, 5, 0, 3, 5, 0][i],
    membership: "Assigned",
    onPremises: false,
  })),
  limitations: [
    "Direct references visible to the connected account only; no unused-group verdict.",
    "Azure RBAC, SharePoint permissions, Exchange workload permissions, Teams policies, Power Platform and external applications are not scanned.",
    "Nested/effective access, dynamic membership expressions, entitlement management, access reviews and historical references are not evaluated.",
  ],
};
if (params.get("state") === "empty") {
  data.tenant = null;
  data.groups = [];
  data.tenants = [];
}
if (params.get("state") === "running" && data.tenant)
  Object.assign(data.tenant, {
    status: "running",
    progress: "Enterprise application assignments: 48/240 groups",
    completedChecks: 2,
    totalChecks: 35,
    startedAt: "2026-09-25T14:32:00Z",
  });
if (params.get("state") === "failed" && data.tenant)
  Object.assign(data.tenant, {
    status: "failed",
    progress:
      "Customer Graph sign-in failed (HTTP 400). Check customer app consent, GDAP roles and the partner OAuth connection.",
  });
function Preview() {
  const [tenantId, setTenantId] = useState(data.tenants.length ? tid : "");
  return (
    <Dashboard
      data={params.get("state") === "loading" ? null : data}
      tenantId={tenantId}
      onTenant={setTenantId}
      busy={false}
      loading={params.get("state") === "loading"}
      error={
        params.get("state") === "denied"
          ? "Could not read saved results. Check Bifrost table access and refresh."
          : ""
      }
      onDiscover={() => {}}
      onScan={() => {}}
      onRefresh={() => {}}
      renderDetails={(g) => (
        <ReferenceList
          references={
            g.referenceCount
              ? [
                  reference,
                  {
                    ...reference,
                    category: "Automatic MDM enrollment",
                    resourceName: "Microsoft Intune enrollment scope",
                    mode: "Include",
                    field: "includedGroups",
                    source:
                      "/beta/policies/mobileDeviceManagementPolicies/example/includedGroups",
                  },
                ]
              : []
          }
        />
      )}
    />
  );
}
createRoot(document.getElementById("root")!).render(<Preview />);
