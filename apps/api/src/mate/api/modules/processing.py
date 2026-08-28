"""Module-processing coordinator - hold a freshly imported log disabled until
every subscribing module has finished precomputing against it.

The lifecycle a log moves through is:

    importing  → parsing the source file
    processing → parsed; the modules that subscribe to the import topic
                 (``log.imported`` / ``ocel.imported``) are precomputing
    ready      → all expected modules reached a terminal job → openable
    failed     → the import itself errored

"All modules" is the *importing user's* installed modules that subscribe to the
import topic (modules are per-user via ``module_installs``). The expected set is
frozen at import time so the decision is deterministic - querying it lazily would
risk a "0 subscribers seen yet → flip to ready early" race. Completion is derived
from the ``Job`` rows (the per-module precompute jobs, linked to the import job by
``parent_job_id``) rather than an in-memory counter, so it survives an API
restart: the boot reconcile re-derives it from the database.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mate.api.db.models import EventLog, Job
from mate.api.events import EventBus
from mate.api.modules.installs import user_module_ids
from mate.api.modules.loader import ModuleLoader

log = structlog.get_logger(__name__)

# A module precompute job is "done" - for the purpose of un-gating the log - once
# it reaches any of these. A failed/cancelled module must not strand the log in
# `processing` forever, so it counts as terminal just like a success.
_TERMINAL_JOB_STATUSES = ("completed", "failed", "cancelled")

# The import job type whose children are the per-module precompute jobs. Mirrors
# ``mate.api.ingest.dispatch.IMPORT_JOB_TYPE`` (kept inline to avoid importing the
# ingest layer into the module layer).
_IMPORT_JOB_TYPE = "event_log.import"


class ModuleProcessingCoordinator:
    """Owns the ``processing`` → ``ready`` transition for imported logs."""

    def __init__(
        self,
        loader: ModuleLoader,
        bus: EventBus,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._loader = loader
        self._bus = bus
        self._sessionmaker = sessionmaker

    async def _closure(
        self, topic: str, user_id: str, session: AsyncSession
    ) -> tuple[set[str], dict[str, set[str]]]:
        """The precompute closure ``(nodes, edges)`` for ``user_id`` on ``topic``.

        The user's installed, job-backed subscribers to ``topic`` plus everything
        chained off their ``<id>.completed`` events - a module loaded for another
        tenant never holds this user's log.
        """
        owned = await user_module_ids(session, user_id)
        return self._loader.precompute_closure(topic, owned)

    async def expected_modules(self, topic: str, user_id: str, session: AsyncSession) -> set[str]:
        """The transitive set of modules a log imported on ``topic`` must wait on.

        The precompute closure (see ``ModuleLoader.precompute_closure``): the
        modules triggered directly by the import event plus everything chained off
        their ``<id>.completed`` events. Frozen at import time so the decision is
        deterministic.
        """
        nodes, _edges = await self._closure(topic, user_id, session)
        return nodes

    async def precompute_plan(
        self, topic: str, user_id: str, session: AsyncSession
    ) -> tuple[set[str], list[dict[str, Any]]]:
        """``(nodes, plan)`` for an import on ``topic``.

        ``plan`` is ``[{"id", "after": [...]}]`` describing the precompute DAG for
        the frontend: roots (direct import subscribers) carry an empty ``after``;
        a chained module lists the upstream module-ids it waits on. Stored on the
        import job's payload so the jobs UI can render waiting/skipped steps.

        The list is in **execution order** (topological), not alphabetical - the
        jobs UI renders it verbatim as a checklist, and a plan that reads
        "chained_mod, precompute_mod" tells the user the opposite of what will
        happen. Sorting here rather than in each client keeps the persisted
        artefact (and the copy the MCP jobs toolset hands the LLM) correct too.
        """
        nodes, edges = await self._closure(topic, user_id, session)
        roots = self._loader.precompute_subscriber_module_ids(topic) & nodes
        # Build `after` once and reuse it for the ordering, so the two can never
        # disagree (a root that also consumes something stays a root in both).
        after = {
            mid: ([] if mid in roots else sorted(edges.get(mid, set()) & nodes)) for mid in nodes
        }
        plan = [{"id": mid, "after": after[mid]} for mid in self._topo_order(after)]
        return nodes, plan

    @staticmethod
    def _topo_order(after: dict[str, list[str]]) -> list[str]:
        """Module ids in dependency order; alphabetical within a layer.

        Kahn's algorithm over the same ``after`` lists the plan publishes. The
        per-layer ``sorted()`` makes the output byte-stable across imports (dict
        iteration order would otherwise leak insertion order into a persisted
        artefact). A cycle - impossible for a `provides`/`consumes` graph the
        loader validated, but cheap to survive - leaves its members unemitted, so
        they are appended rather than silently dropped.
        """
        pending = {mid: set(deps) for mid, deps in after.items()}
        emitted: list[str] = []
        done: set[str] = set()
        while True:
            layer = sorted(mid for mid, deps in pending.items() if deps <= done)
            if not layer:
                break
            for mid in layer:
                emitted.append(mid)
                done.add(mid)
                del pending[mid]
        emitted.extend(sorted(pending))
        return emitted

    async def check_and_finalize(self, log_id: str, session: AsyncSession) -> bool:
        """Flip a ``processing`` log to ``ready`` once its expected modules are done.

        No-op (returns ``False``) when the log is missing, soft-deleted, or not in
        ``processing``. Otherwise compares the expected module-id set against the
        modules whose child precompute job (under the import job) has reached a
        terminal state; when the terminal set covers the expected set the log is
        marked ``ready``, the two processing columns are cleared, and a
        ``log.ready`` event is published. Returns ``True`` when it flipped.
        """
        row = await session.get(EventLog, log_id)
        if row is None or row.deleted_at is not None:
            return False
        if row.status != "processing":
            return False

        expected = set(row.expected_modules or [])
        import_job_id = row.processing_import_job_id

        # Defensive: a processing row with no expected set / no import job can
        # never be un-gated by jobs - treat it as immediately complete so it
        # can't strand. (The ingest handler never writes this shape.)
        if not expected or not import_job_id:
            terminal_covering = True
        else:
            result = await session.execute(
                select(Job.module_id, Job.status).where(
                    Job.parent_job_id == import_job_id,
                    Job.module_id.in_(expected),
                    Job.status.in_(_TERMINAL_JOB_STATUSES),
                )
            )
            terminal_status = {mid: st for (mid, st) in result.all() if mid is not None}
            succeeded = {mid for mid, st in terminal_status.items() if st == "completed"}
            topic = "ocel.imported" if row.log_model == "object_centric" else "log.imported"
            roots = self._loader.precompute_subscriber_module_ids(topic) & expected
            edges = self._loader.precompute_edges(expected)
            settled = self._settled_set(expected, roots, edges, set(terminal_status), succeeded)
            terminal_covering = expected <= settled

        if not terminal_covering:
            return False

        row.status = "ready"
        row.processing_import_job_id = None
        row.expected_modules = None
        await session.commit()

        await self._bus.publish("log.ready", {"user_id": row.user_id, "log_id": log_id})
        log.info("modules.processing.log_ready", log_id=log_id, user_id=row.user_id)
        return True

    @staticmethod
    def _settled_set(
        expected: set[str],
        roots: set[str],
        edges: dict[str, set[str]],
        terminal: set[str],
        succeeded: set[str],
    ) -> set[str]:
        """Expected modules that have either finished (``terminal``) or can never run.

        A chained module is only triggered when an upstream succeeds and emits
        ``<upstream>.completed``. So a non-root module with no terminal job is
        "settled" (skipped) once *every* upstream is settled-without-success - if
        any upstream succeeded it will still be triggered, so we keep waiting.
        Iterates to a fixpoint; every closure node is reachable from a root, so a
        pure dependency cycle cannot strand the gate. A non-root with no known
        producer edge carries no skip signal, so it is *waited on* (as under the
        flat pre-closure semantics) rather than skipped.
        """
        settled = set(terminal)
        changed = True
        while changed:
            changed = False
            for mid in expected:
                if mid in settled or mid in roots:
                    continue
                producers = edges.get(mid, set())
                if not producers:
                    continue  # no producer edge → no skip signal; wait like a root
                if producers & succeeded:
                    continue  # an upstream succeeded → this module will be triggered
                if all(p in settled and p not in succeeded for p in producers):
                    settled.add(mid)  # every upstream settled without success → skip
                    changed = True
        return settled

    async def on_terminal_job(self, payload: dict[str, Any]) -> None:
        """React to a terminal ``job.*`` event by re-checking the parent log.

        The payload is a platform ``job.completed|failed|cancelled`` envelope
        body. We load the job, and if it's a child of an ``event_log.import`` job
        (i.e. a module precompute run) we re-evaluate that log's completion.
        """
        job_id = payload.get("id")
        if not isinstance(job_id, str):
            return
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None or job.parent_job_id is None:
                return
            parent = await session.get(Job, job.parent_job_id)
            if parent is None or parent.type != _IMPORT_JOB_TYPE:
                return
            log_id = parent.payload_json.get("log_id")
            if not isinstance(log_id, str):
                return
            # A successful per-module precompute publishes the reserved
            # `<module_id>.completed` event, carrying `import_job_id` so any
            # chained module's job is parented to the same import group. Only on
            # success: a failed/cancelled upstream emits nothing, so the gate
            # cascade-skips its dependents (see `_settled_set`) instead of
            # waiting on a job that will never be submitted.
            if job.status == "completed" and job.module_id:
                await self._bus.publish(
                    f"{job.module_id}.completed",
                    {
                        "user_id": job.user_id,
                        "log_id": log_id,
                        "import_job_id": parent.id,
                    },
                )
            await self.check_and_finalize(log_id, session)

    async def reconcile_boot(self, session: AsyncSession) -> None:
        """Re-evaluate every ``processing`` log once at startup.

        Module precompute jobs that finished while the API was down (or the
        terminal event that would have triggered the flip was dropped) leave a
        log stuck in ``processing``; this re-derives completion from the ``Job``
        rows so such a log un-gates on the next boot.
        """
        result = await session.execute(select(EventLog.id).where(EventLog.status == "processing"))
        log_ids = [lid for (lid,) in result.all()]
        for log_id in log_ids:
            try:
                await self.check_and_finalize(log_id, session)
            except Exception:
                log.exception("modules.processing.reconcile_failed", log_id=log_id)


_coordinator: ModuleProcessingCoordinator | None = None


def get_coordinator() -> ModuleProcessingCoordinator | None:
    """The process-global coordinator, or ``None`` before startup wires it.

    Returns ``None`` rather than raising so the ingest handler degrades to the
    legacy "ready immediately" path if it ever runs without a coordinator (e.g.
    a bare-runtime test) instead of failing the import.
    """
    return _coordinator


def set_coordinator(coordinator: ModuleProcessingCoordinator | None) -> None:
    global _coordinator
    _coordinator = coordinator
