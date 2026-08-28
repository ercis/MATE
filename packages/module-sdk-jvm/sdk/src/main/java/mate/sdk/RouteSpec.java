package mate.sdk;

import java.util.Locale;

/**
 * An HTTP route the platform mounts at {@code /api/v1/modules/{module_id}{path}}.
 *
 * <p>Bridged routes take no typed request bodies and no declared query
 * parameters (protocol §9) - the module's per-user config is the input
 * channel. The handler's return value (any JSON-representable object) becomes
 * the response body.
 */
public final class RouteSpec {

    private final String method;
    private final String path;
    private final String name;

    private RouteSpec(String method, String path, String name) {
        if (path == null || !path.startsWith("/")) {
            throw new IllegalArgumentException("route path must start with '/': " + path);
        }
        this.method = method;
        this.path = path;
        this.name = name;
    }

    public static RouteSpec get(String path) {
        return new RouteSpec("GET", path, null);
    }

    public static RouteSpec post(String path) {
        return new RouteSpec("POST", path, null);
    }

    public static RouteSpec put(String path) {
        return new RouteSpec("PUT", path, null);
    }

    public static RouteSpec patch(String path) {
        return new RouteSpec("PATCH", path, null);
    }

    public static RouteSpec delete(String path) {
        return new RouteSpec("DELETE", path, null);
    }

    /** Optional display name surfaced in the platform's route metadata. */
    public RouteSpec named(String name) {
        return new RouteSpec(this.method, this.path, name);
    }

    public String method() {
        return method.toUpperCase(Locale.ROOT);
    }

    public String path() {
        return path;
    }

    public String name() {
        return name;
    }
}
