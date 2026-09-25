// The instance supplies the runtime SDK without declarations. This narrow
// contract follows Bifrost's client/src/lib/app-sdk public exports.
declare module "bifrost" {
  import type { ComponentType, PropsWithChildren } from "react";
  export const BifrostProvider: ComponentType<
    PropsWithChildren<{
      baseUrl: string;
      token: string;
      orgScope: string | null;
      appId: string | null;
      theme: "light" | "dark";
      supportsTheme?: boolean;
      onLogout: () => void;
    }>
  >;
  export const BifrostHeader: ComponentType<{ title?: string }>;
  type Query<T> = {
    data: T | null;
    loading: boolean;
    error: Error | null;
    refresh: (input?: Record<string, unknown>) => Promise<T>;
  };
  export function useWorkflowQuery<T>(
    ref: string,
    params?: Record<string, unknown>,
  ): Query<T>;
  export function useWorkflowMutation<T>(ref: string): {
    data: T | null;
    loading: boolean;
    error: Error | null;
    mutate: (input?: Record<string, unknown>) => Promise<T>;
  };
}
