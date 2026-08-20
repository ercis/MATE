import { ImportForm } from "@/components/processes/import-form";
import {
  PageContainer,
  PageHeader,
  PageTitle,
  PageDescription,
} from "@/components/page";

export default function ImportPage() {
  return (
    <PageContainer>
      <PageHeader>
        <div className="space-y-1">
          <PageTitle>Import event log</PageTitle>
          <PageDescription>
            Upload a XES, XES.gz, or CSV file - or import directly from a URL.
          </PageDescription>
        </div>
      </PageHeader>
      <ImportForm />
    </PageContainer>
  );
}
