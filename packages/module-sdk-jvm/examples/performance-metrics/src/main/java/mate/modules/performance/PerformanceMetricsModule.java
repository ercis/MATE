package mate.modules.performance;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.BiFunction;
import mate.sdk.CallArgs;
import mate.sdk.JobSpec;
import mate.sdk.MateModule;
import mate.sdk.ModuleContext;
import mate.sdk.RouteSpec;

/**
 * The bundled example JVM module: performance metrics for a case-centric event
 * log.
 *
 * <p>On every log import a precompute job computes the three result sets and
 * caches them; the routes serve the cache and compute on a miss (so a log
 * imported before this module existed still answers). Each result set is a
 * {@code datasets:} entry in the manifest, so the platform's generic
 * visualizations render them without any module frontend code.
 *
 * <p>The log never leaves the platform: every figure comes from SQL that runs
 * host-side via {@code ctx.eventLog().duckdbFetch}, and only the aggregate
 * crosses the worker socket. See {@link Metrics}.
 */
public final class PerformanceMetricsModule {

    private static final String CACHE_KPIS = "kpis";
    private static final String CACHE_ACTIVITIES = "activities";
    private static final String CACHE_TRANSITIONS = "transitions";

    public static void main(String[] argv) {
        MateModule.builder("performance_java")
                .onEventJob(
                        "compute_on_import",
                        "log.imported",
                        JobSpec.of().progress(true).cancellable(true).title("Performance metrics"),
                        PerformanceMetricsModule::computeJob)
                .route("get_kpis", RouteSpec.get("/kpis"), PerformanceMetricsModule::getKpis)
                .route(
                        "get_activities",
                        RouteSpec.get("/activities"),
                        PerformanceMetricsModule::getActivities)
                .route(
                        "get_transitions",
                        RouteSpec.get("/transitions"),
                        PerformanceMetricsModule::getTransitions)
                .build()
                .run(argv);
    }

    private static Object computeJob(ModuleContext ctx, CallArgs args) {
        ctx.progress().update(0.05, "Reading log schema");
        Set<String> columns = Metrics.columns(ctx);

        ctx.progress().update(0.15, "Computing KPIs");
        Map<String, Object> kpis = Metrics.kpis(ctx, columns);
        ctx.cache().setJson(CACHE_KPIS, kpis);

        ctx.checkCancelled();
        ctx.progress().update(0.5, "Ranking activities");
        Map<String, Object> activities = Metrics.activities(ctx, columns);
        ctx.cache().setJson(CACHE_ACTIVITIES, activities);

        ctx.checkCancelled();
        ctx.progress().update(0.8, "Ranking hand-offs");
        Map<String, Object> transitions = Metrics.transitions(ctx, columns);
        ctx.cache().setJson(CACHE_TRANSITIONS, transitions);

        ctx.logger()
                .info(
                        "performance_metrics_computed",
                        Map.of(
                                "cases", kpis.getOrDefault("cases", 0),
                                "events", kpis.getOrDefault("events", 0),
                                "activity_rows", activities.getOrDefault("row_count", 0),
                                "transition_rows", transitions.getOrDefault("row_count", 0)));
        ctx.progress().update(1.0);

        // The job result is stored per job - keep it to the headline figures
        // rather than a second copy of every table.
        Map<String, Object> result = new LinkedHashMap<>(kpis);
        result.put("kind", "performance_metrics");
        return result;
    }

    private static Object getKpis(ModuleContext ctx, CallArgs args) {
        return cached(ctx, CACHE_KPIS, Metrics::kpis);
    }

    private static Object getActivities(ModuleContext ctx, CallArgs args) {
        return cached(ctx, CACHE_ACTIVITIES, Metrics::activities);
    }

    private static Object getTransitions(ModuleContext ctx, CallArgs args) {
        return cached(ctx, CACHE_TRANSITIONS, Metrics::transitions);
    }

    /**
     * Serve the cached result set, computing (and caching) it on a miss. The
     * platform scopes the cache per {@code (user, log, module)} and forks it per
     * dashboard filter variant, so a filtered card computes and keeps its own
     * numbers instead of reading the whole log's.
     */
    private static Object cached(
            ModuleContext ctx,
            String key,
            BiFunction<ModuleContext, Set<String>, Map<String, Object>> compute) {
        Optional<Object> hit = ctx.cache().getJson(key);
        if (hit.isPresent()) {
            return hit.get();
        }
        Map<String, Object> fresh = compute.apply(ctx, Metrics.columns(ctx));
        ctx.cache().setJson(key, fresh);
        return fresh;
    }

    private PerformanceMetricsModule() {}
}
