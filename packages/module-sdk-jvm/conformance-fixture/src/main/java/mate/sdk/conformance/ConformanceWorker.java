package mate.sdk.conformance;

import java.nio.file.Files;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import mate.sdk.CallArgs;
import mate.sdk.DataWallException;
import mate.sdk.JobSpec;
import mate.sdk.MateModule;
import mate.sdk.ModuleContext;
import mate.sdk.RouteSpec;
import mate.sdk.UnsupportedCacheValueException;

/**
 * The JVM conformance worker: one handler per protocol surface, driven by the
 * platform's pytest suite (apps/api/tests/test_worker_conformance.py). The
 * Python fixture (tests/fixtures/conformance_worker/) mirrors this handler
 * set 1:1 so both runtimes run the identical test matrix.
 */
public final class ConformanceWorker {

    public static void main(String[] argv) {
        MateModule.builder("conformance_worker")
                .route("echo", RouteSpec.get("/echo"), ConformanceWorker::echo)
                .route("snapshot", RouteSpec.get("/snapshot"), ConformanceWorker::snapshot)
                .route("cache_roundtrip", RouteSpec.post("/cache"), ConformanceWorker::cacheRoundtrip)
                .route("cache_pickle", RouteSpec.get("/cache-pickle"), ConformanceWorker::cachePickle)
                .route("bus_emit", RouteSpec.post("/bus"), ConformanceWorker::busEmit)
                .route("progress_ticks", RouteSpec.post("/progress"), ConformanceWorker::progressTicks)
                .route("log_lines", RouteSpec.post("/log"), ConformanceWorker::logLines)
                .route("duckdb", RouteSpec.get("/duckdb"), ConformanceWorker::duckdb)
                .route("materialize_info", RouteSpec.get("/materialize"), ConformanceWorker::materializeInfo)
                .route("datawall_events_path", RouteSpec.get("/datawall"), ConformanceWorker::datawall)
                .route("registry_visible", RouteSpec.get("/registry"), ConformanceWorker::registryVisible)
                .route("big_result", RouteSpec.get("/big"), ConformanceWorker::bigResult)
                .route("boom", RouteSpec.get("/boom"), ConformanceWorker::boom)
                .route("crash", RouteSpec.get("/crash"), ConformanceWorker::crash)
                .routeJob(
                        "cancel_loop",
                        RouteSpec.post("/cancel-loop"),
                        JobSpec.of().progress(true).title("Cancel loop").cancellable(true),
                        ConformanceWorker::cancelLoop)
                .routeJob(
                        "busy_sleep",
                        RouteSpec.post("/busy-sleep"),
                        JobSpec.of().title("Busy sleep"),
                        ConformanceWorker::busySleep)
                .onEventJob(
                        "precompute",
                        "log.imported",
                        JobSpec.of().progress(true).title("Conformance precompute"),
                        ConformanceWorker::precompute)
                .guidance(
                        "Conformance module system prompt.",
                        "conformance:",
                        (ctx, args) -> Map.of("guidance", true, "log_id", ctx.logId()))
                .build()
                .run(argv);
    }

    private static Object echo(ModuleContext ctx, CallArgs args) {
        Map<String, Object> out = new HashMap<>();
        out.put("args", args.args());
        out.put("kwargs", args.kwargs());
        out.put("log_id", ctx.logId());
        out.put("module_id", ctx.moduleId());
        return out;
    }

    private static Object snapshot(ModuleContext ctx, CallArgs args) {
        Map<String, Object> out = new HashMap<>();
        out.put("log_id", ctx.logId());
        out.put("module_id", ctx.moduleId());
        out.put("workdir_exists", Files.isDirectory(ctx.workdir()));
        out.put("config", ctx.config());
        out.put("capabilities", ctx.registry().visible());
        return out;
    }

    private static Object cacheRoundtrip(ModuleContext ctx, CallArgs args) {
        Object value = args.kwarg("value") == null ? Map.of("n", 1) : args.kwarg("value");
        ctx.cache().setJson("conf_key", value);
        boolean existsAfterSet = ctx.cache().exists("conf_key");
        Object got = ctx.cache().getJson("conf_key").orElse(null);
        ctx.cache().delete("conf_key");
        boolean existsAfterDelete = ctx.cache().exists("conf_key");
        Map<String, Object> out = new HashMap<>();
        out.put("got", got);
        out.put("exists_after_set", existsAfterSet);
        out.put("exists_after_delete", existsAfterDelete);
        return out;
    }

    private static Object cachePickle(ModuleContext ctx, CallArgs args) {
        try {
            ctx.cache().getJson("pickled");
            return "read"; // the host gave us something readable - unexpected for the test
        } catch (UnsupportedCacheValueException expected) {
            return "unsupported";
        }
    }

    private static Object busEmit(ModuleContext ctx, CallArgs args) {
        ctx.bus().emit("conformance.pinged", Map.of("n", 1));
        return "ok";
    }

    private static Object progressTicks(ModuleContext ctx, CallArgs args) {
        ctx.progress().update(0.25, "starting");
        ctx.progress().update(0.5, 1.0, "mid", "halfway");
        ctx.progress().update(1.0);
        return "ok";
    }

    private static Object logLines(ModuleContext ctx, CallArgs args) {
        ctx.logger().info("conformance_started", Map.of("n", 1));
        ctx.logger().warning("conformance_warned");
        return "ok";
    }

    private static Object duckdb(ModuleContext ctx, CallArgs args) {
        Object sql = args.kwarg("sql");
        String query = sql == null ? "SELECT 1, 'two'" : String.valueOf(sql);
        return ctx.eventLog().duckdbFetch(query, List.of());
    }

    private static Object materializeInfo(ModuleContext ctx, CallArgs args) throws Exception {
        var path = ctx.eventLog().materialize();
        Map<String, Object> out = new HashMap<>();
        out.put("path", path.toString());
        out.put("size", Files.size(path));
        return out;
    }

    private static Object datawall(ModuleContext ctx, CallArgs args) {
        try {
            return ctx.eventLog().eventsPath().toString();
        } catch (DataWallException expected) {
            return "walled";
        }
    }

    private static Object registryVisible(ModuleContext ctx, CallArgs args) {
        return ctx.registry().visible();
    }

    private static Object bigResult(ModuleContext ctx, CallArgs args) {
        int size = args.kwarg("bytes") instanceof Number n ? n.intValue() : 1_000_000;
        return "x".repeat(Math.max(1, size));
    }

    private static Object boom(ModuleContext ctx, CallArgs args) {
        throw new IllegalStateException("boom");
    }

    private static Object crash(ModuleContext ctx, CallArgs args) {
        Runtime.getRuntime().halt(42); // simulate a hard native crash mid-call
        return null; // unreachable
    }

    private static Object cancelLoop(ModuleContext ctx, CallArgs args) throws Exception {
        while (true) {
            ctx.checkCancelled(); // raises CancelledException once soft-cancelled
            Thread.sleep(25);
        }
    }

    private static Object busySleep(ModuleContext ctx, CallArgs args) throws Exception {
        double seconds =
                args.kwarg("seconds") instanceof Number n ? n.doubleValue() : 30.0;
        Thread.sleep((long) (seconds * 1000));
        return "done";
    }

    private static Object precompute(ModuleContext ctx, CallArgs args) {
        Map<String, Object> payload = args.eventPayload();
        ctx.progress().update(1.0);
        Map<String, Object> out = new HashMap<>();
        out.put("payload_log_id", payload == null ? null : payload.get("log_id"));
        return out;
    }

    private ConformanceWorker() {}
}
