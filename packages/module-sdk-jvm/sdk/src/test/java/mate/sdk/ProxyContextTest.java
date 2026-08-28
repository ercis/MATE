package mate.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import mate.sdk.internal.ProxyContext;
import mate.sdk.internal.RpcTransport;
import org.junit.jupiter.api.Test;

/** Context behaviour against a recording stub transport - no socket needed. */
class ProxyContextTest {

    static final class StubTransport implements RpcTransport {
        final List<Map.Entry<String, Map<String, Object>>> calls = new ArrayList<>();
        final List<String> fireAndForget = new ArrayList<>();
        Object nextResult;
        Throwable nextError;

        @Override
        public Object request(String method, Map<String, Object> params) {
            calls.add(Map.entry(method, params));
            if (nextError != null) {
                Throwable t = nextError;
                nextError = null;
                if (t instanceof RuntimeException runtime) {
                    throw runtime;
                }
                if (t instanceof Error error) {
                    throw error;
                }
                throw new RuntimeException(t);
            }
            return nextResult;
        }

        @Override
        public void requestFireAndForget(String method, Map<String, Object> params) {
            calls.add(Map.entry(method, params));
            fireAndForget.add(method);
        }
    }

    private static Map<String, Object> meta() {
        Map<String, Object> m = new HashMap<>();
        m.put("log_id", "log1");
        m.put("module_id", "conformance");
        m.put("workdir", "/tmp/work");
        m.put("config", Map.of("threshold", 3));
        m.put("capabilities", List.of("discovery", "other.cap"));
        m.put("cache_dir", "/tmp/cache");
        // events_path / cases_path deliberately absent -> data-walled
        return m;
    }

    @Test
    void scalarsAnswerLocally() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());
        assertEquals("log1", ctx.logId());
        assertEquals("conformance", ctx.moduleId());
        assertEquals(3, ctx.config().get("threshold"));
        assertTrue(ctx.registry().has("discovery"));
        assertEquals(0, transport.calls.size());
    }

    @Test
    void missingSnapshotKeysAreDataWalled() {
        ProxyContext ctx = new ProxyContext(new StubTransport(), "tok", meta());
        assertThrows(DataWallException.class, () -> ctx.eventLog().eventsPath());
        assertThrows(DataWallException.class, () -> ctx.eventLog().casesPath());
    }

    @Test
    void cacheEnvelopes() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());

        transport.nextResult = Map.of("kind", "json", "value", Map.of("a", 1));
        Optional<Object> got = ctx.cache().getJson("model");
        assertTrue(got.isPresent());

        transport.nextResult = Map.of("kind", "pickle", "path", "/x.pkl");
        assertThrows(UnsupportedCacheValueException.class, () -> ctx.cache().getJson("model"));

        ctx.cache().setJson("model", List.of(1, 2));
        Map<String, Object> setParams = transport.calls.get(transport.calls.size() - 1).getValue();
        assertEquals("tok", setParams.get("ctx_token"));
        @SuppressWarnings("unchecked")
        Map<String, Object> envelope = (Map<String, Object>) setParams.get("value");
        assertEquals("json", envelope.get("kind"));
        assertEquals(List.of(1, 2), envelope.get("value"));
    }

    @Test
    void checkCancelledPropagatesCancellation() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());
        transport.nextError = new CancelledException();
        assertThrows(CancelledException.class, ctx::checkCancelled);
        assertEquals("ctx.cancel.check", transport.calls.get(0).getKey());
    }

    @Test
    void progressAndLoggerShapes() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());

        ctx.progress().update(0.5, 10.0, "mining", "halfway");
        Map<String, Object> progressParams = transport.calls.get(0).getValue();
        assertEquals(0.5, progressParams.get("current"));
        assertEquals(10.0, progressParams.get("total"));
        assertEquals("mining", progressParams.get("stage"));
        assertEquals("halfway", progressParams.get("message"));

        ctx.logger().info("did_thing", Map.of("n", 2));
        assertEquals(List.of("ctx.logger.log"), transport.fireAndForget);
        Map<String, Object> logParams = transport.calls.get(1).getValue();
        assertEquals("info", logParams.get("level"));
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = (Map<String, Object>) logParams.get("payload");
        assertEquals("did_thing", payload.get("event"));
        assertEquals(2, payload.get("n"));
    }

    @Test
    void duckdbRowsCast() {
        StubTransport transport = new StubTransport();
        ProxyContext ctx = new ProxyContext(transport, "tok", meta());
        transport.nextResult = List.of(List.of("c1", "a"), List.of("c1", "b"));
        List<List<Object>> rows = ctx.eventLog().duckdbFetch("SELECT 1", null);
        assertEquals(2, rows.size());
        assertEquals("a", rows.get(0).get(1));
        Map<String, Object> params = transport.calls.get(0).getValue();
        assertEquals(List.of(), params.get("params"));
    }
}
