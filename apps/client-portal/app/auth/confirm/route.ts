import { NextResponse, type NextRequest } from "next/server";
import type { EmailOtpType } from "@supabase/supabase-js";
import { createClient } from "@/lib/supabase/server";
import { safeNext } from "@/lib/auth";

/**
 * Landing point for every auth email: invite, magic link, password reset.
 * Accepts the token_hash form (recommended email templates, works in any browser)
 * and the PKCE ?code= form (Supabase default templates, same browser only).
 */
export async function GET(request: NextRequest) {
  const url = request.nextUrl;
  const tokenHash = url.searchParams.get("token_hash");
  const type = url.searchParams.get("type") as EmailOtpType | null;
  const code = url.searchParams.get("code");
  const fallback = type === "invite" || type === "recovery" ? "/set-password" : "/";
  const next = safeNext(url.searchParams.get("next"), fallback);

  const supabase = await createClient();
  let ok = false;
  if (tokenHash && type) {
    ok = !(await supabase.auth.verifyOtp({ type, token_hash: tokenHash })).error;
  } else if (code) {
    ok = !(await supabase.auth.exchangeCodeForSession(code)).error;
  }

  const dest = url.clone();
  dest.search = "";
  dest.pathname = ok ? next : "/login";
  if (!ok) dest.searchParams.set("error", "link");
  return NextResponse.redirect(dest);
}
