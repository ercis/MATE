"use client";

import { useEffect } from "react";

/**
 * Last-resort boundary for errors thrown in the root layout itself (where the
 * route-segment error.tsx can't reach). It replaces the whole document, so it
 * renders its own <html>/<body> and uses inline styles – globals.css and the
 * provider stack are exactly what may have failed to mount.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  const btn: React.CSSProperties = {
    cursor: "pointer",
    borderRadius: "0.375rem",
    border: "1px solid #d4d4d8",
    padding: "0.5rem 1rem",
    fontSize: "0.875rem",
    fontWeight: 500,
    textDecoration: "none",
    color: "#111",
    background: "#fff",
  };

  return (
    <html lang="en">
      <body
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "1rem",
          margin: 0,
          padding: "1.5rem",
          textAlign: "center",
          fontFamily: "system-ui, -apple-system, sans-serif",
          background: "#fff",
          color: "#111",
        }}
      >
        <h1 style={{ fontSize: "1.5rem", fontWeight: 600, margin: 0 }}>Something went wrong</h1>
        <p style={{ color: "#666", maxWidth: "28rem", margin: 0 }}>
          The application hit an unexpected error. Reload the page to continue.
        </p>
        <div style={{ display: "flex", gap: "0.75rem" }}>
          <button type="button" onClick={reset} style={btn}>
            Try again
          </button>
          <a href="/login" style={btn}>
            Sign in
          </a>
        </div>
      </body>
    </html>
  );
}
