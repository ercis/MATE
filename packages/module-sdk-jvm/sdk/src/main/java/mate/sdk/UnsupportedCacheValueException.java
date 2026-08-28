package mate.sdk;

/**
 * The cache entry was written by Python code as a pickled object
 * ({@code {"kind": "pickle"}} envelope, protocol §7) and cannot be read from a
 * JVM worker. Only JSON cache values are portable across runtimes; store large
 * binary artefacts as files under {@link Cache#dir()} with JSON metadata instead.
 */
public final class UnsupportedCacheValueException extends IllegalStateException {

    public UnsupportedCacheValueException(String key) {
        super(
                "cache entry '"
                        + key
                        + "' is a Python pickle and cannot be read from a JVM module."
                        + " Only JSON cache values are portable (modules/PROTOCOL.md §7).");
    }
}
