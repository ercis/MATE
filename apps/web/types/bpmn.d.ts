// Minimal type shims for bpmn-io packages that don't ship .d.ts files.
// We only use these via the dynamic module-runtime shim and a single
// BpmnCanvas wrapper, so a permissive `any` shape is enough – bpmn-js
// itself ships proper types.

declare module "bpmn-auto-layout" {
  export function layoutProcess(xml: string): Promise<string>;
}

declare module "diagram-js-minimap" {
  const minimapModule: unknown;
  export default minimapModule;
}
