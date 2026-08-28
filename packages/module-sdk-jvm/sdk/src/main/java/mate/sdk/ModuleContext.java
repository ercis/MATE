package mate.sdk;

import java.nio.file.Path;
import java.util.Map;

/**
 * Everything a handler may touch, scoped to one invocation. The scalar
 * accessors answer from the per-call context snapshot (no round-trip); the
 * service accessors talk JSON-RPC back to the platform (budget ~1-50 ms per
 * call - batch accordingly). See modules/PROTOCOL.md §6-7.
 */
public interface ModuleContext {

    /** Event log this call is scoped to; empty string for log-independent routes. */
    String logId();

    String moduleId();

    /**
     * Per-invocation scratch directory on a filesystem shared with the
     * platform. Deleted by the platform when the call ends.
     */
    Path workdir();

    /** The module's per-user config values (manifest {@code config_schema}). */
    Map<String, Object> config();

    EventLog eventLog();

    Cache cache();

    Bus bus();

    Registry registry();

    Progress progress();

    WorkerLogger logger();

    /**
     * Cooperative cancellation poll: throws {@link CancelledException} when
     * the platform has cancelled this job. Call it inside compute loops that
     * don't otherwise touch {@code ctx.*} - after the soft-cancel grace window
     * (~3 s) the platform hard-kills the whole worker process instead.
     */
    void checkCancelled();
}
