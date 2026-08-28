package mate.sdk;

/**
 * One module handler - the body behind a route, an event subscription, a job,
 * or the AI {@code guidance_payload} hook.
 *
 * <p>The return value must be JSON-representable (maps/lists/strings/numbers/
 * booleans/null, or a Jackson-serializable POJO) - it crosses the worker
 * socket as the {@code call} result. Handlers run on their own daemon thread;
 * overlapping invocations are possible, so share state carefully. Throwing
 * {@link CancelledException} (or letting a {@code ctx.*} call's one propagate)
 * records the job as cancelled; any other {@link Throwable} records it failed
 * with the message.
 */
@FunctionalInterface
public interface Handler {

    Object handle(ModuleContext ctx, CallArgs args) throws Exception;
}
