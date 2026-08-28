import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Cheap, Edge-safe auth gate: only checks that a session cookie is *present*.
// We can't decode the session here – with the server-side store (Option 3) the
// token lives on disk, unreachable from the Edge runtime. Real validation
// happens server-side: RSCs via `auth()` (Node runtime) and the API via JWKS.
// /logout must stay public: it's the escape hatch that clears a wedged session
// cookie, so it has to run even when the gate would otherwise interfere.
const PUBLIC_PATHS = ["/login", "/logout", "/api/auth"];
const SESSION_COOKIES = ["authjs.session-token", "__Secure-authjs.session-token"];

export default function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const isPublic = PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  if (isPublic) return NextResponse.next();

  // Match the base cookie name OR a chunk (`<name>.0`, `.1`, …). Auth.js splits
  // a session cookie larger than ~4KB into numbered chunks, so an exact
  // `cookies.has(name)` would miss a large client-side session and wrongly
  // bounce every request to /login.
  const hasSession = req.cookies
    .getAll()
    .some((c) => SESSION_COOKIES.some((name) => c.name === name || c.name.startsWith(`${name}.`)));
  if (!hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("callbackUrl", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // Skip Next.js static assets, the favicon, and the icon SVG; everything else
  // goes through the gate.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
