import { SettingsTabs } from "@/components/settings/settings-tabs";
import { PageContainer } from "@/components/page";

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <PageContainer>
      <SettingsTabs />
      <div className="pt-6">{children}</div>
    </PageContainer>
  );
}
