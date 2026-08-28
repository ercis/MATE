/**
 * The config-schema dialect modules author their settings in.
 *
 * JSON-Schema-flavoured, but deliberately not JSON Schema: it is a *form*
 * description, so it carries presentation hints (`ui.widget`, `ui.group`,
 * `enumLabels`) that JSON Schema has no place for. The backend passes it
 * through as an opaque dict — this file is the only specification of what the
 * keys mean, so keep it accurate.
 *
 * The same dialect describes three things:
 *   - a module's own settings   (`manifest.config_schema`)
 *   - a dashboard card's options (`manifest.frontend.widgets[].config_schema`)
 *   - a generic viz's options    (the viz registry)
 *
 * They used to be rendered by two different components that had drifted apart,
 * each supporting controls the other didn't. One renderer now: `SchemaForm`.
 */

export interface PropSchema {
  /** `integer` and `number` are both numeric — do not gate a control on
   * `"number"` alone, which silently downgraded every integer slider in the
   * codebase to a text input. */
  type?: "number" | "integer" | "string" | "boolean" | "array";
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  step?: number;
  /** Allowed values. Presence alone implies a picker. */
  enum?: string[];
  /** Display labels parallel to `enum`; falls back to the raw value. */
  enumLabels?: string[];
  ui?: {
    /** Force a control. Otherwise it is inferred from `type`/`enum`:
     *   `select` | `segmented` | `slider` | `switch` | `number` | `text`
     *   | `multiselect` (with `type: "array"` + `enum`) */
    widget?: string;
    /** Section heading to file this field under. Fields with no group render
     * first, ungrouped. */
    group?: string;
    /** Longer explanation shown behind a ⓘ rather than always-on helper text.
     * Use for the "why", and `description` for the "what". */
    help?: string;
  };
}

export interface ConfigSchema {
  properties?: Record<string, PropSchema>;
}

/** Sizing. `compact` fits the card inspector's narrow column; `comfortable` is
 * the roomier module settings page. Only spacing and control height differ —
 * never which controls exist. */
export type SchemaFormDensity = "compact" | "comfortable";

/** Seed a values object from a schema's declared defaults. */
export function schemaDefaults(schema: ConfigSchema | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, prop] of Object.entries(schema?.properties ?? {})) {
    if (prop.default !== undefined) out[key] = prop.default;
  }
  return out;
}

/** Group fields by `ui.group`, preserving declaration order within each group
 * and putting ungrouped fields first. */
export function groupProperties(
  properties: Record<string, PropSchema>,
): [string, [string, PropSchema][]][] {
  const groups = new Map<string, [string, PropSchema][]>();
  for (const entry of Object.entries(properties)) {
    const group = entry[1].ui?.group ?? "";
    const bucket = groups.get(group);
    if (bucket) bucket.push(entry);
    else groups.set(group, [entry]);
  }
  // Ungrouped first, then declaration order of the groups themselves.
  return [...groups.entries()].sort((a, b) => (a[0] === "" ? -1 : b[0] === "" ? 1 : 0));
}
