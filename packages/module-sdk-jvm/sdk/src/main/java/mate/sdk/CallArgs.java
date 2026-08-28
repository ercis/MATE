package mate.sdk;

import java.util.List;
import java.util.Map;

/**
 * The positional + keyword arguments of one handler invocation (protocol §5,
 * {@code call.params.args} / {@code call.params.kwargs}). Values are plain
 * JSON-mapped Java: {@code Map}, {@code List}, {@code String}, {@code Number},
 * {@code Boolean}, or {@code null}.
 */
public final class CallArgs {

    private final List<Object> args;
    private final Map<String, Object> kwargs;

    public CallArgs(List<Object> args, Map<String, Object> kwargs) {
        this.args = args == null ? List.of() : args;
        this.kwargs = kwargs == null ? Map.of() : kwargs;
    }

    public List<Object> args() {
        return args;
    }

    public Map<String, Object> kwargs() {
        return kwargs;
    }

    /** Positional arg at {@code index}, or {@code null} when absent. */
    public Object arg(int index) {
        return index >= 0 && index < args.size() ? args.get(index) : null;
    }

    /** Keyword arg, or {@code null} when absent. */
    public Object kwarg(String name) {
        return kwargs.get(name);
    }

    /**
     * For event handlers: the bus event's payload object (delivered as
     * {@code args[0]}), or {@code null} for non-event invocations.
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> eventPayload() {
        Object first = arg(0);
        return first instanceof Map ? (Map<String, Object>) first : null;
    }
}
