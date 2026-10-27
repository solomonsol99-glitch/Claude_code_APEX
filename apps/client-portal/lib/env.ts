import "server-only";
import { z } from "zod";

/**
 * Server-only settings. Nothing here is exposed to the browser: there are no
 * NEXT_PUBLIC_ variables, and all Supabase calls run on the server.
 */
const schema = z.object({
  SUPABASE_URL: z.url(),
  SUPABASE_PUBLISHABLE_KEY: z.string().min(20),
  SUPABASE_SECRET_KEY: z.string().min(20),
  APP_URL: z.url().optional(),
  DATA_PROVIDER: z.enum(["sample", "coreset"]).default("sample"),
});

export type Env = z.infer<typeof schema>;

let cached: Env | undefined;

/** Read lazily so `next build` works without secrets; fails clearly at request time if any are missing. */
export function env(): Env {
  if (cached) return cached;
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    const missing = parsed.error.issues.map((i) => i.path.join(".")).join(", ");
    throw new Error(`Missing or invalid environment variables: ${missing}. See apps/client-portal/.env.example.`);
  }
  cached = parsed.data;
  return cached;
}

/** Public base URL used in auth email links. Falls back to the Vercel deployment URL on previews. */
export function appUrl(): string {
  const explicit = env().APP_URL;
  if (explicit) return explicit.replace(/\/$/, "");
  const vercel = process.env.VERCEL_BRANCH_URL ?? process.env.VERCEL_URL;
  if (vercel) return `https://${vercel}`;
  return "http://localhost:3000";
}
