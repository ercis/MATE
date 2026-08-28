/**
 * Canonical event-name registry. Centralising these prevents drift between
 * `process_imported`, `process-import-done`, and `ProcessImportFinished`
 * landing in the same table over time.
 */
export const EV = {
  // Page / navigation
  PAGE_VIEW: "page_view",
  PAGE_LEAVE: "page_leave",

  // Auto-captured clicks
  CLICK: "click",

  // UI-log capture (Abb & Rehse reference model)
  INPUT_CHANGE: "input_change",
  HOTKEY: "hotkey",
  POINTER_TRACE: "pointer_trace",
  SCROLL_DEPTH: "scroll_depth",
  CLIPBOARD: "clipboard",
  DRAG: "drag",
  VIEWPORT_RESIZE: "viewport_resize",
  VISIBILITY: "visibility",

  // Onboarding funnel (only fires after opt-in)
  ONBOARDING_STARTED: "onboarding_started",
  ONBOARDING_STEP_COMPLETED: "onboarding_step_completed",
  ONBOARDING_FINISHED: "onboarding_finished",
  ONBOARDING_SKIPPED: "onboarding_skipped",

  // Privacy / settings
  ANALYTICS_OPT_IN: "analytics_opt_in",
  ANALYTICS_OPT_OUT: "analytics_opt_out",
  ANALYTICS_DATA_WIPED: "analytics_data_wiped",

  // Process / data
  PROCESS_IMPORT_STARTED: "process_import_started",
  // Fired when the user confirms the column mapping. `changed_roles` > 0 means
  // the deliberate step actually caught something the resolver got wrong.
  PROCESS_IMPORT_MAPPING_CONFIRMED: "process_import_mapping_confirmed",
  PROCESS_IMPORT_FINISHED: "process_import_finished",
  PROCESS_OPENED: "process_opened",
  PROCESS_DELETED: "process_deleted",
  PROCESS_EDIT_SAVED: "process_edit_saved",

  // Modules
  MODULE_OPENED: "module_opened",
  MODULE_PANEL_TAB_CHANGED: "module_panel_tab_changed",

  // AI
  AI_CHAT_SENT: "ai_chat_sent",
  AI_GUIDANCE_REQUESTED: "ai_guidance_requested",
  AI_NAV_SUGGESTED: "ai_nav_suggested",
  AI_NAV_CLICKED: "ai_nav_clicked",
  AI_ACTION_SUGGESTED: "ai_action_suggested",
  AI_ACTION_APPLIED: "ai_action_applied",

  // UI chrome
  SIDEBAR_TOGGLED: "sidebar_toggled",
  THEME_CHANGED: "theme_changed",

  // Errors / perf
  CLIENT_ERROR: "client_error",
  WEB_VITAL: "web_vital",
} as const;

export type EventName = (typeof EV)[keyof typeof EV];
