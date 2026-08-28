package mate.modules.performance;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import mate.sdk.ModuleContext;

/**
 * Every metric this module reports, computed by SQL that runs host-side.
 *
 * <p>{@code ctx.eventLog().duckdbFetch} is the documented default data path
 * (modules/README.md §13): the platform executes the query against the
 * filter-applied {@code events} view and ships back JSON rows, so the worker
 * needs no Parquet reader, no DuckDB JDBC driver and no local copy of the log.
 * Every query here aggregates in SQL - what crosses the socket is one row per
 * activity or hand-off, never per event, which keeps the result far under the
 * protocol's 256 MiB frame ceiling on any log size.
 */
final class Metrics {

    /** Row caps: a pathological log must not blow up the JSON frame or the UI. */
    private static final int MAX_ACTIVITY_ROWS = 500;
    private static final int MAX_TRANSITION_ROWS = 300;

    private static final double SECONDS_PER_DAY = 86_400.0;

    private Metrics() {}

    /**
     * Canonical + log-specific column names of the {@code events} view,
     * lowercased. The canonical trio is guaranteed by the manifest's
     * {@code required_columns}; {@code end_timestamp} and {@code resource} are
     * optional, so every read of them is gated on this set.
     */
    static Set<String> columns(ModuleContext ctx) {
        List<List<Object>> rows =
                ctx.eventLog()
                        .duckdbFetch("SELECT column_name FROM (DESCRIBE SELECT * FROM events)", List.of());
        Set<String> names = new LinkedHashSet<>();
        for (List<Object> row : rows) {
            if (!row.isEmpty() && row.get(0) != null) {
                names.add(String.valueOf(row.get(0)).toLowerCase(Locale.ROOT));
            }
        }
        return names;
    }

    /**
     * Headline timing figures. Returned as a flat map of numbers, which the
     * platform's {@code shape: kpi} adapter turns into KPI tiles as-is - keys
     * become labels, so they are named for humans.
     */
    static Map<String, Object> kpis(ModuleContext ctx, Set<String> columns) {
        boolean service = columns.contains("end_timestamp");
        List<List<Object>> rows =
                ctx.eventLog()
                        .duckdbFetch(
                                eventsCte(service)
                                        + ", per_case AS ("
                                        + "  SELECT case_id, COUNT(*) AS n_events, MIN(ts) AS started,"
                                        + "         MAX(ts) AS ended, SUM(service) AS service_seconds"
                                        + "  FROM e GROUP BY case_id"
                                        + "), variants AS ("
                                        + "  SELECT string_agg(activity, chr(1) ORDER BY ts) AS trace"
                                        + "  FROM e GROUP BY case_id"
                                        + ")"
                                        + " SELECT (SELECT COUNT(*) FROM per_case),"
                                        + "        (SELECT COUNT(*) FROM e),"
                                        + "        (SELECT COUNT(DISTINCT activity) FROM e),"
                                        + "        (SELECT COUNT(DISTINCT trace) FROM variants),"
                                        + "        (SELECT AVG(n_events) FROM per_case),"
                                        + "        (SELECT AVG(ended - started) FROM per_case),"
                                        + "        (SELECT median(ended - started) FROM per_case),"
                                        + "        (SELECT quantile_cont(ended - started, 0.9) FROM per_case),"
                                        + "        (SELECT MAX(ended - started) FROM per_case),"
                                        + "        (SELECT AVG(service_seconds) FROM per_case),"
                                        + "        (SELECT MIN(started) FROM per_case),"
                                        + "        (SELECT MAX(ended) FROM per_case)",
                                List.of());
        List<Object> r = rows.isEmpty() ? List.of() : rows.get(0);

        Double cases = num(at(r, 0));
        Double cycleAvg = num(at(r, 5));
        Double serviceAvg = num(at(r, 9));
        Double first = num(at(r, 10));
        Double last = num(at(r, 11));

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("kind", "performance_metrics");
        put(out, "cases", cases);
        put(out, "events", num(at(r, 1)));
        put(out, "activities", num(at(r, 2)));
        put(out, "variants", num(at(r, 3)));
        put(out, "events_per_case", round(num(at(r, 4)), 2));
        put(out, "cycle_time_avg_seconds", round(cycleAvg, 1));
        put(out, "cycle_time_median_seconds", round(num(at(r, 6)), 1));
        put(out, "cycle_time_p90_seconds", round(num(at(r, 7)), 1));
        put(out, "cycle_time_max_seconds", round(num(at(r, 8)), 1));

        // Wall-clock span of the whole log, and the cases/day it sustained over
        // it. A single-instant log (span 0) has no meaningful throughput.
        if (first != null && last != null) {
            double spanDays = (last - first) / SECONDS_PER_DAY;
            put(out, "log_span_days", round(spanDays, 2));
            if (cases != null && spanDays > 0) {
                put(out, "throughput_cases_per_day", round(cases / spanDays, 2));
            }
        }
        // Only meaningful with an explicit end_timestamp: without it every event
        // is an instant and "processing time" would be a constant zero.
        if (service && serviceAvg != null) {
            put(out, "processing_time_avg_seconds", round(serviceAvg, 1));
            if (cycleAvg != null && cycleAvg > 0) {
                double waiting = 1.0 - (serviceAvg / cycleAvg);
                put(out, "waiting_time_share", round(Math.max(0.0, Math.min(1.0, waiting)), 4));
            }
        }
        if (columns.contains("resource")) {
            List<List<Object>> res =
                    ctx.eventLog()
                            .duckdbFetch(
                                    "SELECT COUNT(DISTINCT resource) FROM events"
                                            + " WHERE resource IS NOT NULL",
                                    List.of());
            put(out, "resources", res.isEmpty() ? null : num(at(res.get(0), 0)));
        }
        return out;
    }

    /**
     * Per-activity dwell time - the wait from an event until the case's next
     * event, i.e. where cases actually lose time. Ranked by total dwell, so the
     * head of the table is the bottleneck list.
     */
    static Map<String, Object> activities(ModuleContext ctx, Set<String> columns) {
        boolean service = columns.contains("end_timestamp");
        List<List<Object>> rows =
                ctx.eventLog()
                        .duckdbFetch(
                                eventsCte(service)
                                        + ", step AS ("
                                        + "  SELECT case_id, activity, service,"
                                        + "         LEAD(ts) OVER (PARTITION BY case_id ORDER BY ts) - ts AS dwell"
                                        + "  FROM e"
                                        + ")"
                                        + " SELECT activity, COUNT(*), COUNT(DISTINCT case_id),"
                                        + "        COALESCE(SUM(dwell), 0), AVG(dwell), median(dwell),"
                                        + "        quantile_cont(dwell, 0.9), AVG(service)"
                                        + " FROM step GROUP BY activity"
                                        + " ORDER BY 4 DESC, 2 DESC LIMIT "
                                        + MAX_ACTIVITY_ROWS,
                                List.of());

        double totalDwell = 0;
        for (List<Object> row : rows) {
            Double d = num(at(row, 3));
            totalDwell += d == null ? 0 : d;
        }
        List<Map<String, Object>> out = new ArrayList<>(rows.size());
        for (List<Object> row : rows) {
            Double total = num(at(row, 3));
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("activity", str(at(row, 0)));
            put(entry, "occurrences", num(at(row, 1)));
            put(entry, "cases", num(at(row, 2)));
            put(entry, "total_dwell_seconds", round(total, 1));
            put(entry, "avg_dwell_seconds", round(num(at(row, 4)), 1));
            put(entry, "median_dwell_seconds", round(num(at(row, 5)), 1));
            put(entry, "p90_dwell_seconds", round(num(at(row, 6)), 1));
            if (service) {
                put(entry, "avg_processing_seconds", round(num(at(row, 7)), 1));
            }
            put(entry, "dwell_share", share(total, totalDwell));
            out.add(entry);
        }
        return table("performance_activities", "activities", out, MAX_ACTIVITY_ROWS);
    }

    /**
     * Directly-follows hand-offs ranked by the time cases spend between the two
     * activities - the transition-level view of the same loss.
     */
    static Map<String, Object> transitions(ModuleContext ctx, Set<String> columns) {
        List<List<Object>> rows =
                ctx.eventLog()
                        .duckdbFetch(
                                eventsCte(columns.contains("end_timestamp"))
                                        + ", pairs AS ("
                                        + "  SELECT case_id, activity AS src,"
                                        + "         LEAD(activity) OVER (PARTITION BY case_id ORDER BY ts) AS dst,"
                                        + "         LEAD(ts) OVER (PARTITION BY case_id ORDER BY ts) - ts AS gap"
                                        + "  FROM e"
                                        + ")"
                                        + " SELECT src, dst, COUNT(*), COUNT(DISTINCT case_id),"
                                        + "        COALESCE(SUM(gap), 0), AVG(gap), median(gap),"
                                        + "        quantile_cont(gap, 0.9)"
                                        + " FROM pairs WHERE dst IS NOT NULL GROUP BY src, dst"
                                        + " ORDER BY 5 DESC, 3 DESC LIMIT "
                                        + MAX_TRANSITION_ROWS,
                                List.of());

        double totalWait = 0;
        for (List<Object> row : rows) {
            Double d = num(at(row, 4));
            totalWait += d == null ? 0 : d;
        }
        List<Map<String, Object>> out = new ArrayList<>(rows.size());
        for (List<Object> row : rows) {
            Double total = num(at(row, 4));
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("from_activity", str(at(row, 0)));
            entry.put("to_activity", str(at(row, 1)));
            put(entry, "occurrences", num(at(row, 2)));
            put(entry, "cases", num(at(row, 3)));
            put(entry, "total_wait_seconds", round(total, 1));
            put(entry, "avg_wait_seconds", round(num(at(row, 5)), 1));
            put(entry, "median_wait_seconds", round(num(at(row, 6)), 1));
            put(entry, "p90_wait_seconds", round(num(at(row, 7)), 1));
            put(entry, "wait_share", share(total, totalWait));
            out.add(entry);
        }
        return table("performance_transitions", "transitions", out, MAX_TRANSITION_ROWS);
    }

    /**
     * The shared row source: canonical columns only, timestamps flattened to
     * epoch seconds so nothing datetime-shaped has to survive JSON, and rows
     * with a null in the trio dropped (they cannot be ordered or attributed).
     * {@code service} is the event's own duration when the log carries an
     * {@code end_timestamp}, and a typed NULL otherwise - that way both variants
     * share one query text and the aggregates degrade to NULL by themselves.
     */
    private static String eventsCte(boolean service) {
        String serviceExpr =
                service
                        ? "epoch(CAST(\"end_timestamp\" AS TIMESTAMP))"
                                + " - epoch(CAST(\"timestamp\" AS TIMESTAMP))"
                        : "CAST(NULL AS DOUBLE)";
        return "WITH e AS ("
                + "  SELECT case_id, activity, epoch(CAST(\"timestamp\" AS TIMESTAMP)) AS ts,"
                + "         "
                + serviceExpr
                + " AS service"
                + "  FROM events"
                + "  WHERE case_id IS NOT NULL AND activity IS NOT NULL AND \"timestamp\" IS NOT NULL"
                + ")";
    }

    /**
     * A {@code shape: table} response. The platform's adapter takes the first
     * value that is a list of objects as the rows, so the row list goes in
     * first and every other key is a scalar.
     */
    private static Map<String, Object> table(
            String kind, String key, List<Map<String, Object>> rows, int cap) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put(key, rows);
        out.put("kind", kind);
        out.put("row_count", rows.size());
        out.put("truncated", rows.size() >= cap);
        return out;
    }

    private static Object at(List<Object> row, int index) {
        return index < row.size() ? row.get(index) : null;
    }

    private static String str(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    /** JSON numbers arrive as Integer/Long/Double; SQL NULLs as null. */
    private static Double num(Object value) {
        if (value instanceof Number n) {
            double d = n.doubleValue();
            return Double.isFinite(d) ? d : null;
        }
        return null;
    }

    private static Double round(Double value, int decimals) {
        if (value == null) {
            return null;
        }
        double factor = Math.pow(10, decimals);
        return Math.round(value * factor) / factor;
    }

    private static Double share(Double part, double total) {
        if (part == null || total <= 0) {
            return null;
        }
        return round(part / total, 4);
    }

    /**
     * Skip nulls: an absent key just doesn't render, while a null KPI would be a
     * tile reading "-" and a null table cell an empty column.
     */
    private static void put(Map<String, Object> target, String key, Double value) {
        if (value == null) {
            return;
        }
        // Whole numbers go out as integers so counts don't render as "20.0".
        if (value == Math.rint(value) && Math.abs(value) < 9.0e15) {
            target.put(key, (long) (double) value);
        } else {
            target.put(key, value);
        }
    }
}
