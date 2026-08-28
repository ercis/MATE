package mate.sdk;

/**
 * The touched surface is deliberately unavailable in this context (protocol
 * §6, "data-wall rule"): restricted invocations (AI/MCP paths) omit the raw
 * event-data keys from the context snapshot, and the SDK surfaces that as this
 * error instead of guessing paths. There is no way around it from module code
 * - compute from {@code duckdbFetch}/aggregates instead, or return less.
 */
public final class DataWallException extends IllegalStateException {

    public DataWallException(String surface) {
        super(
                surface
                        + " is not available in this invocation context (restricted/data-walled)."
                        + " See modules/PROTOCOL.md §6.");
    }
}
