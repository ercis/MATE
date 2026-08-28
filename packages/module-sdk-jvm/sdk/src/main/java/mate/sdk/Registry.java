package mate.sdk;

import java.util.List;
import java.util.Map;

/**
 * Cross-module capability registry. {@link #has} and {@link #visible} answer
 * locally from the per-call snapshot; {@link #call} is a host RPC.
 *
 * <p>NOTE: capability RPC is specified but currently dormant platform-side -
 * {@link #call} raises a host error until the platform binds capability
 * handlers. {@link #has} is reliable (module ids + advertised capabilities).
 */
public interface Registry {

    /** Whether the user has {@code moduleIdOrCapability} available. */
    boolean has(String moduleIdOrCapability);

    /** Module ids + capability names visible to the calling user. */
    List<String> visible();

    /** Invoke another module's capability ({@code ctx.registry.call}). */
    Object call(String capability, Map<String, ?> kwargs);
}
