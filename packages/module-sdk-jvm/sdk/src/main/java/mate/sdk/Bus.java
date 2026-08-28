package mate.sdk;

import java.util.Map;

/** Fire-and-forget platform event bus (protocol §7, {@code ctx.bus.emit}). */
public interface Bus {

    /**
     * Publish {@code payload} on {@code topic}. The topic must be declared in
     * the manifest's {@code provides:}. Tenant fields ({@code user_id},
     * {@code log_id}) are stamped by the platform - workers cannot set them.
     */
    void emit(String topic, Map<String, ?> payload);
}
