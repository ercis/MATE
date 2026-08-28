"use client";

/**
 * The import form moved to `components/processes/import/` when it grew into a
 * staged flow (upload → confirm mapping → live import). This shim keeps the
 * original import path working for the import page and the onboarding wizard.
 */
export { ImportFlow as ImportForm } from "@/components/processes/import/import-flow";
