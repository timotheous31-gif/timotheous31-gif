/** Container health check. Reports only that the Next.js server is serving. */
export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json({ status: "ok" });
}
