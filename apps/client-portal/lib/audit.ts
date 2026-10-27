import "server-only";
import { createServiceClient } from "@/lib/supabase/service";

export type AuditEntry = {
  action: string;
  via: "portal" | "ai" | "admin" | "system";
  orgId?: string | null;
  userId?: string | null;
  apiKeyId?: string | null;
  /** Filters, sizes, ids. Never record-level data. */
  detail?: Record<string, unknown>;
};

export async function audit(entry: AuditEntry): Promise<void> {
  const { error } = await createServiceClient().from("audit_log").insert({
    action: entry.action,
    via: entry.via,
    org_id: entry.orgId ?? null,
    actor_user_id: entry.userId ?? null,
    actor_api_key_id: entry.apiKeyId ?? null,
    detail: entry.detail ?? {},
  });
  if (error) throw new Error(`audit log write failed: ${error.message}`);
}
