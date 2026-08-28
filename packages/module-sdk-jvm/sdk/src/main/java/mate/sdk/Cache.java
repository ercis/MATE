package mate.sdk;

import java.nio.file.Path;
import java.util.Optional;

/**
 * The module's per-{@code (user, log, module)} result cache. Invalidated by
 * the platform on log re-import and on module config changes.
 *
 * <p>From a JVM worker only JSON values are supported (protocol §7): reading
 * an entry a Python module pickled throws {@link UnsupportedCacheValueException}.
 * For large binary artefacts, write files under {@link #dir()} and cache JSON
 * metadata pointing at them.
 */
public interface Cache {

    /**
     * Cached JSON value. {@link Optional#empty()} when the key is missing (or
     * holds JSON {@code null}).
     *
     * @throws UnsupportedCacheValueException when the entry is a Python pickle
     */
    Optional<Object> getJson(String key);

    /** Store a JSON-representable value (maps/lists/strings/numbers/booleans). */
    void setJson(String key, Object value);

    boolean exists(String key);

    void delete(String key);

    /**
     * The cache directory on the shared filesystem - for file-shaped results.
     *
     * @throws DataWallException in restricted invocation contexts
     */
    Path dir();
}
