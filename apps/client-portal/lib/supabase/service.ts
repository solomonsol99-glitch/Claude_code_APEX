import "server-only";
import { createClient } from "@supabase/supabase-js";
import { env } from "@/lib/env";

/**
 * Service-role client. Bypasses Row Level Security, so only call it from server code
 * that has already checked who the caller is and what they may do.
 */
export function createServiceClient() {
  const { SUPABASE_URL, SUPABASE_SECRET_KEY } = env();
  return createClient(SUPABASE_URL, SUPABASE_SECRET_KEY, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}
