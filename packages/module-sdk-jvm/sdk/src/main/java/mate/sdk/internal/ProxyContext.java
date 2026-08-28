package mate.sdk.internal;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import mate.sdk.Bus;
import mate.sdk.Cache;
import mate.sdk.DataWallException;
import mate.sdk.EventLog;
import mate.sdk.ModuleContext;
import mate.sdk.Progress;
import mate.sdk.Registry;
import mate.sdk.RpcException;
import mate.sdk.UnsupportedCacheValueException;
import mate.sdk.WorkerLogger;

/**
 * The {@link ModuleContext} a handler sees inside the worker: scalars answer
 * from the per-call snapshot ({@code call.params.ctx}, protocol §6), services
 * forward over the transport as {@code ctx.*} requests (§7).
 */
public final class ProxyContext implements ModuleContext {

    private final RpcTransport transport;
    private final String ctxToken;
    private final Map<String, Object> meta;

    public ProxyContext(RpcTransport transport, String ctxToken, Map<String, Object> meta) {
        this.transport = transport;
        this.ctxToken = ctxToken;
        this.meta = meta == null ? Map.of() : meta;
    }

    @Override
    public String logId() {
        return meta.get("log_id") == null ? "" : String.valueOf(meta.get("log_id"));
    }

    @Override
    public String moduleId() {
        return String.valueOf(meta.get("module_id"));
    }

    @Override
    public Path workdir() {
        Object dir = meta.get("workdir");
        if (dir == null) {
            throw new DataWallException("workdir");
        }
        return Path.of(String.valueOf(dir));
    }

    @Override
    @SuppressWarnings("unchecked")
    public Map<String, Object> config() {
        Object config = meta.get("config");
        return config instanceof Map ? (Map<String, Object>) config : Map.of();
    }

    @Override
    public EventLog eventLog() {
        return new EventLogProxy();
    }

    @Override
    public Cache cache() {
        return new CacheProxy();
    }

    @Override
    public Bus bus() {
        return (topic, payload) -> request("ctx.bus.emit", params("topic", topic, "payload", payload));
    }

    @Override
    public Registry registry() {
        return new RegistryProxy();
    }

    @Override
    public Progress progress() {
        return new ProgressProxy();
    }

    @Override
    public WorkerLogger logger() {
        return new LoggerProxy();
    }

    @Override
    public void checkCancelled() {
        // The host's guard raises the cancel sentinel when this job is flagged;
        // `request` maps it to CancelledException. Otherwise the result (false)
        // is meaningless.
        request("ctx.cancel.check", params());
    }

    // -- service proxies ----------------------------------------------------

    private final class EventLogProxy implements EventLog {

        @Override
        @SuppressWarnings("unchecked")
        public List<List<Object>> duckdbFetch(String sql, List<Object> params) {
            Object rows =
                    request(
                            "ctx.event_log.duckdb_fetch",
                            params("sql", sql, "params", params == null ? List.of() : params));
            List<List<Object>> result = new ArrayList<>();
            if (rows instanceof List<?> outer) {
                for (Object row : outer) {
                    result.add(row instanceof List ? (List<Object>) row : List.of(row));
                }
            }
            return result;
        }

        @Override
        public Path materialize() {
            Object path = request("ctx.event_log.materialize", params());
            return Path.of(String.valueOf(path));
        }

        @Override
        public Path eventsPath() {
            return snapshotPath("events_path", "event_log.events_path");
        }

        @Override
        public Path casesPath() {
            return snapshotPath("cases_path", "event_log.cases_path");
        }

        @Override
        public Object activeFilter() {
            return meta.get("active_filter");
        }

        private Path snapshotPath(String key, String surface) {
            Object value = meta.get(key);
            if (value == null) {
                throw new DataWallException(surface);
            }
            return Path.of(String.valueOf(value));
        }
    }

    private final class CacheProxy implements Cache {

        @Override
        public Optional<Object> getJson(String key) {
            Object envelope = request("ctx.cache.get", params("key", key));
            if (envelope instanceof Map<?, ?> map) {
                Object kind = map.get("kind");
                if ("pickle".equals(kind)) {
                    throw new UnsupportedCacheValueException(key);
                }
                if ("json".equals(kind)) {
                    return Optional.ofNullable(map.get("value"));
                }
            }
            return Optional.ofNullable(envelope);
        }

        @Override
        public void setJson(String key, Object value) {
            Map<String, Object> envelope = new HashMap<>();
            envelope.put("kind", "json");
            envelope.put("value", value);
            request("ctx.cache.set", params("key", key, "value", envelope));
        }

        @Override
        public boolean exists(String key) {
            return Boolean.TRUE.equals(request("ctx.cache.exists", params("key", key)));
        }

        @Override
        public void delete(String key) {
            request("ctx.cache.delete", params("key", key));
        }

        @Override
        public Path dir() {
            Object dir = meta.get("cache_dir");
            if (dir == null) {
                throw new DataWallException("cache.dir");
            }
            return Path.of(String.valueOf(dir));
        }
    }

    private final class RegistryProxy implements Registry {

        @Override
        public boolean has(String moduleIdOrCapability) {
            return visible().contains(moduleIdOrCapability);
        }

        @Override
        @SuppressWarnings("unchecked")
        public List<String> visible() {
            Object caps = meta.get("capabilities");
            return caps instanceof List ? (List<String>) caps : List.of();
        }

        @Override
        public Object call(String capability, Map<String, ?> kwargs) {
            return request(
                    "ctx.registry.call",
                    params("capability", capability, "kwargs", kwargs == null ? Map.of() : kwargs));
        }
    }

    private final class ProgressProxy implements Progress {

        @Override
        public void update(double current) {
            update(current, null, null, null);
        }

        @Override
        public void update(double current, String message) {
            update(current, null, null, message);
        }

        @Override
        public void update(double current, Double total, String stage, String message) {
            request(
                    "ctx.progress.update",
                    params("current", current, "total", total, "stage", stage, "message", message));
        }
    }

    private final class LoggerProxy implements WorkerLogger {

        @Override
        public void debug(String event, Map<String, ?> fields) {
            log("debug", event, fields);
        }

        @Override
        public void info(String event, Map<String, ?> fields) {
            log("info", event, fields);
        }

        @Override
        public void warning(String event, Map<String, ?> fields) {
            log("warning", event, fields);
        }

        @Override
        public void error(String event, Map<String, ?> fields) {
            log("error", event, fields);
        }

        private void log(String level, String event, Map<String, ?> fields) {
            Map<String, Object> payload = new HashMap<>();
            if (fields != null) {
                payload.putAll(fields);
            }
            payload.put("event", event);
            transport.requestFireAndForget(
                    "ctx.logger.log", params("level", level, "payload", payload));
        }
    }

    // -- plumbing -----------------------------------------------------------

    private Object request(String method, Map<String, Object> params) {
        return transport.request(method, params);
    }

    /** Small params builder that always carries the ctx token; null values kept
     * (the host treats explicit null and absent alike for optionals). */
    private Map<String, Object> params(Object... keyValues) {
        if (keyValues.length % 2 != 0) {
            throw new IllegalArgumentException("params() takes key/value pairs");
        }
        Map<String, Object> map = new HashMap<>();
        map.put("ctx_token", ctxToken);
        for (int i = 0; i < keyValues.length; i += 2) {
            map.put(String.valueOf(keyValues[i]), keyValues[i + 1]);
        }
        return map;
    }
}
