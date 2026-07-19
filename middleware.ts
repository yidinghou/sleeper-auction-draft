import { jwtVerify } from "jose";
import { NextRequest, NextResponse } from "next/server";

function getSecretKey() {
  const secret = process.env.SESSION_SECRET;
  if (!secret) throw new Error("SESSION_SECRET is not set");
  return new TextEncoder().encode(secret);
}

export async function middleware(req: NextRequest) {
  const token = req.cookies.get("session")?.value;

  if (!token) {
    return NextResponse.redirect(new URL("/login", req.url));
  }

  try {
    const { payload } = await jwtVerify(token, getSecretKey());
    if (req.nextUrl.pathname.startsWith("/admin") && !payload.isAdmin) {
      return NextResponse.redirect(new URL("/draft", req.url));
    }
  } catch {
    return NextResponse.redirect(new URL("/login", req.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/draft/:path*", "/admin/:path*"],
};
