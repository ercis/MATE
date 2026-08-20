import { AdminTabs } from "@/components/admin/admin-tabs";
import { PageContainer, PageTitle, PageDescription } from "@/components/page";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <PageContainer>
      <header className="space-y-2 pb-6">
        <PageTitle>Admin</PageTitle>
        <PageDescription>
          Platform-wide administration across every user – activity, event logs,
          storage, and exports.
        </PageDescription>
      </header>
      <AdminTabs />
      <div className="pt-6">{children}</div>
    </PageContainer>
  );
}
