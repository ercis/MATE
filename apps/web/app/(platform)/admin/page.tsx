import { redirect } from "next/navigation";

/** Bare `/admin` lands on the Overview dashboard (the admin home). */
export default function AdminIndexPage() {
  redirect("/admin/overview");
}
