/**
 * Makes a Coreset admin. Sends an invite email with a "set your password" link;
 * never creates or stores a password.
 *
 *   SUPABASE_URL=... SUPABASE_SECRET_KEY=... APP_URL=https://app.coresetdata.com \
 *     npm run seed:admin -- peterashton9@icloud.com
 */
import { createClient } from "@supabase/supabase-js";

async function main() {
  const email = (process.argv[2] ?? "").trim().toLowerCase();
  const { SUPABASE_URL, SUPABASE_SECRET_KEY, APP_URL } = process.env;
  if (!email || !email.includes("@")) throw new Error("Usage: npm run seed:admin -- <email>");
  if (!SUPABASE_URL || !SUPABASE_SECRET_KEY || !APP_URL) {
    throw new Error("Set SUPABASE_URL, SUPABASE_SECRET_KEY and APP_URL first.");
  }

  const supabase = createClient(SUPABASE_URL, SUPABASE_SECRET_KEY, { auth: { persistSession: false } });

  let userId: string | undefined;
  let invited = false;
  const { data, error } = await supabase.auth.admin.inviteUserByEmail(email, {
    redirectTo: `${APP_URL.replace(/\/$/, "")}/auth/confirm?next=/set-password`,
  });
  if (data?.user) {
    userId = data.user.id;
    invited = true;
  } else if (error && /already/i.test(error.message)) {
    // Existing account: find it and promote it.
    for (let page = 1; !userId; page++) {
      const { data: list, error: listErr } = await supabase.auth.admin.listUsers({ page, perPage: 200 });
      if (listErr) throw listErr;
      userId = list.users.find((u) => u.email?.toLowerCase() === email)?.id;
      if (list.users.length < 200) break;
    }
  } else if (error) {
    throw error;
  }
  if (!userId) throw new Error(`Could not find or invite ${email}.`);

  const { error: upErr } = await supabase.from("profiles").update({ is_admin: true }).eq("id", userId);
  if (upErr) throw upErr;
  await supabase.from("audit_log").insert({ via: "system", action: "admin.seed", actor_user_id: userId, detail: { email, invited } });

  console.log(
    invited
      ? `Invited ${email} as a Coreset admin. They'll get an email to set a password, then set up two-step verification.`
      : `${email} already had an account and is now a Coreset admin. Use "Forgot password?" on the sign-in page if needed.`,
  );
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
