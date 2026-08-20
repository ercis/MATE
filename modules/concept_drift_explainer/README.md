# Concept Drift Explainer

Port of the master-thesis prototype (Schaffner, "Explaining Concept Drifts in
Business Processes: An LLM-Based Approach to Context-Aware Sense-Making in
Process Mining", University of Münster) into a Mate module.

## What it does

For each drift detected by the [cv4cdd](../cv4cdd/) module, the CDE retrieves
evidence from a per-log corpus of enterprise documents (PDF, DOCX, PPTX, PNG,
JPG) embedded in Pinecone, then synthesises a ranked, evidence-backed list of
hypothesised causes. A chatbot lets analysts ask follow-up questions grounded
in the same evidence state.

This module keeps its AI config **fully isolated** from the platform's global
**Settings → AI** – it owns its own OpenAI key and only ever talks to OpenAI.

## Setup

All configuration lives under **Settings → Modules → Concept Drift Explainer**:

1. In the **AI models** card, paste your **OpenAI** API key, click **Check**,
   then pick a chat model (e.g. `gpt-4o-mini`) and an embedding model (e.g.
   `text-embedding-3-small`). The key is stored with this module only.
2. Save a **Pinecone** API key and (optionally) index name in the
   **Configuration** card.
3. On a process page, open the panel, upload one or more dated context
   documents (filename must start with `YYYY-MM-DD_`), and click **Re-index**.
4. Select a drift detected by cv4cdd and click **Run analysis**.

## Scope

This first cut covers the core retrieval + ranking + explanation pipeline plus
the follow-up chatbot. The original repository's drift-linker (multi-drift
meta-analysis) and DOCX report generation are not yet ported.

## Costs and guardrails

- The pipeline auto-refreshes the drift list when cv4cdd finishes, but it
  never auto-runs the LLM stages – explanation runs are user-driven.
- LLM responses are cached per `(prompt, model)` in the module's per-log
  result cache so re-runs of the same drift don't repeatedly hit the API.

## Provenance

The original repository lives at
[github.com/janschaffner/concept_drift_explainer](https://github.com/janschaffner/concept_drift_explainer).
The agents, prompts, and Pydantic schemas in this module are the same as the
original – only the credential plumbing, on-disk document storage, and graph
entry point have been adapted for the platform.
