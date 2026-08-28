"use client";

import { SchemaForm } from "@/components/schema-form/schema-form";
import type { ConfigSchema, PropSchema } from "@/components/schema-form/types";

// Re-exported so the module detail page and the admin Controls editor keep
// their existing imports. The dialect is defined once, in `schema-form/types`.
export type { ConfigSchema, PropSchema };

/**
 * A module's own settings, rendered from its `config_schema`.
 *
 * Thin wrapper over `SchemaForm` — this file used to carry a second, divergent
 * implementation of the same dialect. Folding it in fixed two live bugs on the
 * module settings page: `type: integer` sliders rendered as text inputs (the
 * old renderer gated sliders on `type === "number"`), and booleans rendered as
 * a text box containing the word "true" (there was no switch at all).
 *
 * `disabled` makes every input read-only — used for the admin-locked and
 * user-read-only views.
 */
export function ModuleConfigForm({
  properties,
  values,
  onChange,
  disabled = false,
}: {
  properties: Record<string, PropSchema>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  disabled?: boolean;
}) {
  return (
    <SchemaForm
      schema={{ properties }}
      values={values}
      onChange={onChange}
      disabled={disabled}
      density="comfortable"
    />
  );
}
