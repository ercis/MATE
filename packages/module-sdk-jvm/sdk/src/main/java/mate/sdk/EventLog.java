package mate.sdk;

import java.nio.file.Path;
import java.util.List;

/**
 * The case-centric event log bound to this invocation.
 *
 * <p>Two data paths (protocol §7):
 * <ul>
 *   <li>{@link #duckdbFetch} - SQL executes on the platform against the real
 *       log; rows come back as JSON. Zero local dependencies; the right choice
 *       for aggregations. Views: {@code events} (committed/ephemeral filter
 *       already applied - use this), {@code events_src} (raw), {@code cases}.
 *       Canonical columns: {@code case_id}, {@code activity}, {@code timestamp}
 *       (+ optional {@code end_timestamp}, {@code resource}, {@code cost},
 *       {@code role}, {@code lifecycle}, and the log's own extra columns).
 *   <li>{@link #materialize} - the platform writes the filter-applied log as a
 *       Parquet file on the shared filesystem and returns its path; read it
 *       with any Parquet/Arrow library (DuckDB JDBC's {@code read_parquet} is
 *       the documented recommendation - not an SDK dependency).
 * </ul>
 */
public interface EventLog {

    /**
     * Run SQL host-side, return rows as JSON values. Keep results comfortably
     * under the 256 MiB frame ceiling - aggregate in SQL, or use
     * {@link #materialize()} for bulk data. {@code params} are DuckDB
     * positional parameters ({@code ?}), may be null/empty.
     */
    List<List<Object>> duckdbFetch(String sql, List<Object> params);

    /**
     * Platform-written Parquet file of the (filter-applied) event log, under
     * {@link ModuleContext#workdir()} - cleaned up with it.
     */
    Path materialize();

    /**
     * Path of the log's raw, UNFILTERED {@code events.parquet}. Prefer
     * {@link #materialize()} unless you handle {@link #activeFilter()}
     * yourself.
     *
     * @throws DataWallException in restricted invocation contexts
     */
    Path eventsPath();

    /**
     * Path of {@code cases.parquet} when the log has one.
     *
     * @throws DataWallException in restricted invocation contexts (or when the
     *     log has no cases file)
     */
    Path casesPath();

    /** The committed event-filter definition (JSON array), or {@code null}. */
    Object activeFilter();
}
