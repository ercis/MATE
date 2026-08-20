import { toast } from "sonner";

import { rawFetch } from "@/lib/api";

/** Fetch an authenticated file and save it client-side as `filename`.
 *
 * Shared by the admin export and the per-log download. Surfaces the server's
 * `detail` for non-2xx responses (e.g. a 409 "original upload is not available")
 * and special-cases 403 with the admin-role hint. */
export async function downloadBlob(path: string, filename: string): Promise<void> {
  const res = await rawFetch(path);
  if (res.status === 403) {
    toast.error("This download requires the admin role.");
    return;
  }
  if (!res.ok) {
    let detail = `Download failed (${res.status}).`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // Non-JSON error body – keep the generic message.
    }
    toast.error(detail);
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
