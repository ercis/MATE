"use client";

/**
 * DOM introspection for the UI log (Abb & Rehse reference model).
 *
 * Every captured interaction is described as an action on a *target object*
 * inside a UI hierarchy: the atomic element, its enclosing semantic groups,
 * the application, and the system. These helpers derive that context from a
 * DOM node: a stable CSS-ish selector (element identity), the target's
 * descriptive attributes, its current state, and the ancestor group chain.
 *
 * Privacy: `isSecretField` marks inputs whose values must never be read
 * (passwords, credential autofill, anything opted out via
 * `data-no-capture-value` or the global `data-no-track`).
 */

export interface TargetInfo {
  tag: string;
  id: string | null;
  testid: string | null;
  role: string | null;
  label: string | null;
  text: string | null;
  type: string | null;
}

export interface UiGroupInfo {
  kind: string;
  id: string | null;
  label: string | null;
}

const SELECTOR_MAX_CHARS = 400;
const GROUP_MAX_DEPTH = 6;

// Semantic containers that count as UI groups, innermost first. Mirrors the
// paper's ui_group component: dialogs, forms, navigation, landmarks, tables,
// and explicit anchors the app already ships (`data-tour`, dashboard cards).
const GROUP_SELECTOR = [
  "dialog",
  "[role='dialog']",
  "[role='alertdialog']",
  "[role='menu']",
  "form",
  "nav",
  "aside",
  "header",
  "footer",
  "main",
  "table",
  "section[id]",
  "article[id]",
  "[data-ui-group]",
  "[data-tour]",
  "[data-card-id]",
].join(",");

/** Stable, capped selector path for element identity (paper: element selectors). */
export function cssPath(el: Element): string {
  const parts: string[] = [];
  let node: Element | null = el;
  while (node && node !== document.documentElement && parts.length < 12) {
    const tag = node.tagName.toLowerCase();
    if (node.id) {
      // An id anchors the path - everything above it is redundant.
      parts.unshift(`${tag}#${node.id}`);
      break;
    }
    const testid = node.getAttribute("data-testid");
    if (testid) {
      parts.unshift(`${tag}[data-testid="${testid}"]`);
      node = node.parentElement;
      continue;
    }
    let part = tag;
    const parent: Element | null = node.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(
        (c) => c.tagName === node!.tagName,
      );
      if (siblings.length > 1) {
        part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
    }
    parts.unshift(part);
    node = parent;
  }
  return parts.join(">").slice(0, SELECTOR_MAX_CHARS);
}

function firstNonEmpty(...values: Array<string | null | undefined>): string | null {
  for (const v of values) {
    const t = v?.trim();
    if (t) return t;
  }
  return null;
}

/** Descriptive attributes of the target element (paper: ui_element). */
export function elementTarget(el: HTMLElement): TargetInfo {
  const label = firstNonEmpty(
    el.getAttribute("aria-label"),
    el.getAttribute("data-track-name"),
    el.getAttribute("title"),
    el.getAttribute("placeholder"),
    el.getAttribute("alt"),
  );
  return {
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    testid: el.getAttribute("data-testid"),
    role: el.getAttribute("role"),
    label,
    text: (el.textContent ?? "").trim().slice(0, 80) || null,
    type: el.getAttribute("type"),
  };
}

/** Fields whose values must never be captured, regardless of settings. */
export function isSecretField(el: HTMLElement): boolean {
  if (el.closest("[data-no-capture-value],[data-no-track]")) return true;
  const type = (el.getAttribute("type") || "").toLowerCase();
  if (type === "password" || type === "hidden") return true;
  const autocomplete = (el.getAttribute("autocomplete") || "").toLowerCase();
  return (
    autocomplete.includes("password") ||
    autocomplete.startsWith("cc-") ||
    autocomplete === "one-time-code"
  );
}

/**
 * Current state of a stateful element (paper: ui_element.current_state).
 * `captureValue` reflects the `capture_inputs` setting; secret fields are
 * value-redacted unconditionally (only their length is recorded).
 */
export function elementState(
  el: HTMLElement,
  captureValue: boolean,
): Record<string, unknown> | null {
  const state: Record<string, unknown> = {};
  if (el instanceof HTMLInputElement) {
    if (el.type === "checkbox" || el.type === "radio") state.checked = el.checked;
    else if (captureValue && !isSecretField(el)) state.value = el.value.slice(0, 256);
    else state.value_len = el.value.length;
    if (el.disabled) state.disabled = true;
    if (el.readOnly) state.readonly = true;
  } else if (el instanceof HTMLTextAreaElement) {
    if (captureValue && !isSecretField(el)) state.value = el.value.slice(0, 256);
    else state.value_len = el.value.length;
    if (el.disabled) state.disabled = true;
  } else if (el instanceof HTMLSelectElement) {
    state.value = el.selectedOptions[0]?.textContent?.trim().slice(0, 160) ?? null;
    // Paper working example: dropdowns also record the selectable options.
    state.options = Array.from(el.options)
      .slice(0, 20)
      .map((o) => (o.textContent ?? "").trim().slice(0, 60));
    if (el.disabled) state.disabled = true;
  } else {
    const expanded = el.getAttribute("aria-expanded");
    const selected = el.getAttribute("aria-selected");
    const checked = el.getAttribute("aria-checked");
    if (expanded != null) state.expanded = expanded === "true";
    if (selected != null) state.selected = selected === "true";
    if (checked != null) state.checked = checked === "true";
    if ((el as HTMLButtonElement).disabled) state.disabled = true;
  }
  return Object.keys(state).length ? state : null;
}

/** Ancestor UI-group chain, innermost first (paper: nested ui_groups). */
export function uiGroups(el: Element): UiGroupInfo[] {
  const groups: UiGroupInfo[] = [];
  let node: Element | null = el.closest(GROUP_SELECTOR);
  while (node && groups.length < GROUP_MAX_DEPTH) {
    const kind =
      node.getAttribute("data-ui-group") ||
      node.getAttribute("role") ||
      node.tagName.toLowerCase();
    groups.push({
      kind,
      id:
        node.id ||
        node.getAttribute("data-tour") ||
        node.getAttribute("data-card-id") ||
        null,
      label: firstNonEmpty(
        node.getAttribute("aria-label"),
        node.querySelector(":scope > h1, :scope > h2, :scope > h3, legend")?.textContent,
      )?.slice(0, 80) ?? null,
    });
    node = node.parentElement?.closest(GROUP_SELECTOR) ?? null;
  }
  return groups;
}

/** Paper 5.2.1: the activity name is a function of action type + target id. */
export function activityName(actionType: string, target: TargetInfo): string {
  const what = target.role || target.tag;
  const which = target.label || target.text || target.id || target.testid;
  return (which ? `${actionType} ${what} "${which.slice(0, 60)}"` : `${actionType} ${what}`).slice(
    0,
    200,
  );
}
