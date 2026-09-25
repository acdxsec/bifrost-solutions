export type Reference = {
  groupId: string;
  category: string;
  resourceName: string;
  resourceId: string;
  field: string;
  mode: string;
  source: string;
  assignmentId: string;
  intent: string;
  policyState: string;
  filterId: string;
  filterType: string;
  roleDefinitionId: string;
  directoryScopeId: string;
  appRoleId: string;
};
export type Group = {
  groupId: string;
  name: string;
  description: string;
  membership: string;
  onPremises: boolean;
  referenceCount: number;
};
export type Check = {
  category: string;
  status: string;
  examined: number;
  errorCount: number;
  errors: { source: string; message: string }[];
};
export type Tenant = {
  tenantId: string;
  name: string;
  status: string;
  snapshotId?: string;
  scannedAt?: string;
  startedAt?: string;
  progress: string;
  completedChecks?: number;
  totalChecks?: number;
  coverage?: Check[];
  groupCount?: number;
  referenceCount?: number;
};
export type Inventory = {
  tenants: { tenantId: string; name: string }[];
  discoveredAt?: string;
  tenant: Tenant | null;
  groups: Group[];
  limitations: string[];
};
export type Details = {
  tenantId: string;
  groupId: string;
  snapshotId: string;
  references: Reference[];
};
