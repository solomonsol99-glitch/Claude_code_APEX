export function GET() {
  return Response.json({ ok: true, provider: process.env.DATA_PROVIDER ?? "sample" });
}
