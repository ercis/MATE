package mate.sdk;

/**
 * A {@code ctx.*} call failed on the host side (or the connection to the host
 * broke). The message is the host's error text; there is no cause chain across
 * the process boundary.
 */
public class RpcException extends RuntimeException {

    public RpcException(String message) {
        super(message);
    }

    public RpcException(String message, Throwable cause) {
        super(message, cause);
    }
}
