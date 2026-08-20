"use client";

import type { EventLogDetail } from "@/lib/api-types";

import { GeneralSection } from "./general-section";
import { ColumnRolesSection } from "./column-roles-section";
import { SchemaSection } from "./schema-section";
import { QualitySection } from "./quality-section";
import { MaintenanceSection } from "./maintenance-section";
import { EditHistorySection } from "./edit-history-section";

export interface SettingsTabProps {
  logId: string;
  log: EventLogDetail;
}

export function SettingsTab({ logId, log }: SettingsTabProps) {
  // Column roles, schema, data quality and the event-edit history are all
  // case-centric concepts (and their endpoints reject OCEL logs). Object-centric
  // logs only get the model-agnostic General + Maintenance sections.
  const objectCentric = log.log_model === "object_centric";
  return (
    <div className="space-y-8">
      <GeneralSection logId={logId} log={log} />
      {!objectCentric && (
        <>
          <ColumnRolesSection logId={logId} log={log} />
          <SchemaSection logId={logId} log={log} />
          <QualitySection logId={logId} />
          <EditHistorySection logId={logId} />
        </>
      )}
      <MaintenanceSection log={log} />
    </div>
  );
}
