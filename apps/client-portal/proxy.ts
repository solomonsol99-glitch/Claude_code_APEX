import { NextResponse, type NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/proxy";

// Reachable without a session. Everything else needs one. This is a fast first gate;
// every page, action and route re-checks the user on the server.
const PUBLIC = ["/login", "/forgot-password", "/auth/", "/api/health", "/api/mcp"];

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const { response, userId } = await updateSession(request);
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(p.endsWith("/") ? p : p + "/"));

  if (!userId && !isPublic) {
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    login.search = "";
    return NextResponse.redirect(login);
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
