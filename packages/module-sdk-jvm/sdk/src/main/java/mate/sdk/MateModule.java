package mate.sdk;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import mate.sdk.internal.ParentDeathGuard;
import mate.sdk.internal.ProxyContext;
import mate.sdk.internal.Protocol;
import mate.sdk.internal.WireConnection;

/**
 * A Mate module worker. Register handlers, then hand over the process:
 *
 * <pre>{@code
 * public static void main(String[] argv) {
 *     MateModule.builder("alpha_miner_java")
 *         .onEventJob("mine", "log.imported",
 *             JobSpec.of().progress(true).title("Alpha Miner"), Impl::mine)
 *         .route("get_model", RouteSpec.get("/model"), Impl::getModel)
 *         .build()
 *         .run(argv);
 * }
 * }</pre>
 *
 * <p>{@link #run} connects to the platform socket from the launch argv, sends
 * the {@code ready} handshake advertising every registered handler, serves
 * {@code call}s until the platform hangs up, then exits. It never returns
 * normally. See modules/PROTOCOL.md for the wire contract.
 */
public final class MateModule {

    private static final Pattern HANDLER_NAME = Pattern.compile("[a-z_][a-z0-9_]*");

    private final String moduleId;
    private final Map<String, HandlerEntry> handlers;
    private final String guidanceSystemPrompt;
    private final String guidanceUserPrefix;

    private MateModule(Builder builder) {
        this.moduleId = builder.moduleId;
        this.handlers = new LinkedHashMap<>(builder.handlers);
        this.guidanceSystemPrompt = builder.guidanceSystemPrompt;
        this.guidanceUserPrefix = builder.guidanceUserPrefix;
    }

    public static Builder builder(String moduleId) {
        return new Builder(moduleId);
    }

    /**
     * Connect, handshake, serve until the platform hangs up, exit. Call this
     * last in {@code main} - it takes over the process.
     */
    public void run(String[] argv) {
        if (argv.length < 2) {
            System.err.println(
                    "usage: <worker> <socket_path> <module_folder> (launched by the Mate platform)");
            System.exit(2);
        }
        Path socketPath = Path.of(argv[argv.length - 2]);
        ParentDeathGuard.install();
        try (WireConnection conn = WireConnection.connect(socketPath)) {
            conn.register("call", params -> dispatchCall(conn, params));
            conn.register("ping", params -> true);
            conn.register(
                    "shutdown",
                    params -> {
                        // Reply first (the dispatcher writes the response right
                        // after this returns), then leave; the grace beats the
                        // host's 2s shutdown timeout comfortably.
                        Thread exit =
                                new Thread(
                                        () -> {
                                            try {
                                                Thread.sleep(100);
                                            } catch (InterruptedException ignored) {
                                                // fall through to exit
                                            }
                                            Runtime.getRuntime().halt(0);
                                        },
                                        "mate-shutdown");
                            exit.setDaemon(true);
                            exit.start();
                            return true;
                        });
            conn.notify("ready", readyParams());
            conn.runLoop(); // returns when the platform closes the socket
        } catch (Exception exc) {
            System.err.println("mate-sdk worker failed: " + exc);
            exc.printStackTrace();
            System.exit(1);
        }
        System.exit(0);
    }

    private Object dispatchCall(WireConnection conn, Map<String, Object> params) throws Exception {
        String attr = String.valueOf(params.get("handler"));
        HandlerEntry entry = handlers.get(attr);
        if (entry == null) {
            throw new IllegalArgumentException("unknown handler '" + attr + "'");
        }
        String ctxToken = String.valueOf(params.get("ctx_token"));
        @SuppressWarnings("unchecked")
        Map<String, Object> ctxMeta =
                params.get("ctx") instanceof Map ? (Map<String, Object>) params.get("ctx") : Map.of();
        @SuppressWarnings("unchecked")
        List<Object> args =
                params.get("args") instanceof List ? (List<Object>) params.get("args") : List.of();
        @SuppressWarnings("unchecked")
        Map<String, Object> kwargs =
                params.get("kwargs") instanceof Map
                        ? (Map<String, Object>) params.get("kwargs")
                        : Map.of();
        ModuleContext ctx = new ProxyContext(conn, ctxToken, ctxMeta);
        return entry.handler.handle(ctx, new CallArgs(args, kwargs));
    }

    private Map<String, Object> readyParams() {
        List<Map<String, Object>> handlerList = new ArrayList<>();
        for (Map.Entry<String, HandlerEntry> registered : handlers.entrySet()) {
            HandlerEntry entry = registered.getValue();
            if ("guidance_payload".equals(registered.getKey())) {
                continue; // advertised via `guidance`, not the handler list
            }
            Map<String, Object> meta = new HashMap<>();
            meta.put("attr", registered.getKey());
            if (entry.route != null) {
                Map<String, Object> route = new HashMap<>();
                route.put("method", entry.route.method());
                route.put("path", entry.route.path());
                route.put("name", entry.route.name());
                meta.put("route", route);
            }
            if (entry.eventTopic != null) {
                meta.put("on_event", Map.of("topic", entry.eventTopic));
            }
            if (entry.job != null) {
                Map<String, Object> job = new HashMap<>();
                job.put("progress", entry.job.isProgress());
                job.put("priority", entry.job.getPriority());
                job.put("cancellable", entry.job.isCancellable());
                job.put("result_url", entry.job.getResultUrl());
                job.put("title", entry.job.getTitle());
                job.put("subtitle", entry.job.getSubtitle());
                meta.put("job", job);
            }
            handlerList.add(meta);
        }
        Map<String, Object> params = new HashMap<>();
        params.put("protocol", Protocol.VERSION);
        params.put("handlers", handlerList);
        if (guidanceSystemPrompt != null || guidanceUserPrefix != null) {
            Map<String, Object> guidance = new HashMap<>();
            guidance.put("system_prompt", guidanceSystemPrompt);
            guidance.put("user_prefix", guidanceUserPrefix);
            params.put("guidance", guidance);
        } else {
            params.put("guidance", null);
        }
        return params;
    }

    private record HandlerEntry(RouteSpec route, String eventTopic, JobSpec job, Handler handler) {}

    public static final class Builder {

        private final String moduleId;
        private final Map<String, HandlerEntry> handlers = new LinkedHashMap<>();
        private String guidanceSystemPrompt;
        private String guidanceUserPrefix;

        private Builder(String moduleId) {
            if (moduleId == null || !HANDLER_NAME.matcher(moduleId).matches()) {
                throw new IllegalArgumentException(
                        "module id must be lowercase snake_case: " + moduleId);
            }
            this.moduleId = moduleId;
        }

        /** Plain HTTP route. {@code name} must be unique lowercase snake_case. */
        public Builder route(String name, RouteSpec route, Handler handler) {
            return add(name, new HandlerEntry(route, null, null, handler));
        }

        /** Route that enqueues a job and immediately returns {@code {"job_id": ...}}. */
        public Builder routeJob(String name, RouteSpec route, JobSpec job, Handler handler) {
            return add(name, new HandlerEntry(route, null, job, handler));
        }

        /** Fire-and-forget bus subscription (topic must be in the manifest's {@code consumes:}). */
        public Builder onEvent(String name, String topic, Handler handler) {
            return add(name, new HandlerEntry(null, topic, null, handler));
        }

        /** Job-backed subscription - the precompute pattern (one job per event). */
        public Builder onEventJob(String name, String topic, JobSpec job, Handler handler) {
            return add(name, new HandlerEntry(null, topic, job, handler));
        }

        /**
         * Declare the module's AI guidance: the static prompt strings plus the
         * handler behind {@code guidance_payload} (called by the platform's AI
         * integration; return a JSON-representable summary of module state).
         */
        public Builder guidance(String systemPrompt, String userPrefix, Handler payloadHandler) {
            this.guidanceSystemPrompt = systemPrompt;
            this.guidanceUserPrefix = userPrefix;
            handlers.put("guidance_payload", new HandlerEntry(null, null, null, payloadHandler));
            return this;
        }

        private Builder add(String name, HandlerEntry entry) {
            if (name == null || !HANDLER_NAME.matcher(name).matches()) {
                throw new IllegalArgumentException(
                        "handler name must be lowercase snake_case: " + name);
            }
            if (handlers.putIfAbsent(name, entry) != null) {
                throw new IllegalArgumentException("duplicate handler name: " + name);
            }
            return this;
        }

        public MateModule build() {
            if (handlers.isEmpty()) {
                throw new IllegalStateException("a module needs at least one handler");
            }
            return new MateModule(this);
        }
    }
}
