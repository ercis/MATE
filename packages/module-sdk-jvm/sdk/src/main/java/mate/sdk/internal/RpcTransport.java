package mate.sdk.internal;

import java.util.Map;

/**
 * What {@link ProxyContext} needs from the connection - split out so context
 * behaviour is unit-testable without a live socket.
 */
public interface RpcTransport {

    /**
     * Send one request and block for its result. Throws
     * {@link mate.sdk.CancelledException} when the platform answers with the
     * cancel sentinel, {@link mate.sdk.RpcException} on any other error.
     */
    Object request(String method, Map<String, Object> params);

    /** Send a request without waiting for the reply (logger path). */
    void requestFireAndForget(String method, Map<String, Object> params);
}
