package mate.sdk;

import java.util.Map;

/**
 * Structured platform logger: entries land in the platform log and the
 * per-job log ring (admin Jobs tab). Fire-and-forget - calls don't wait for
 * the platform's ack. {@code event} is a short machine-readable slug; details
 * go in {@code fields}. (Plain stdout/stderr is also captured, line-wise.)
 */
public interface WorkerLogger {

    void debug(String event, Map<String, ?> fields);

    void info(String event, Map<String, ?> fields);

    void warning(String event, Map<String, ?> fields);

    void error(String event, Map<String, ?> fields);

    default void debug(String event) {
        debug(event, Map.of());
    }

    default void info(String event) {
        info(event, Map.of());
    }

    default void warning(String event) {
        warning(event, Map.of());
    }

    default void error(String event) {
        error(event, Map.of());
    }
}
