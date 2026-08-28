package mate.sdk.internal;

/** Wire-protocol constants (modules/PROTOCOL.md). */
public final class Protocol {

    /** Version this SDK speaks, advertised in {@code ready.params.protocol}. */
    public static final int VERSION = 1;

    /**
     * Substring the platform puts into an RPC error's {@code message} when the
     * job was soft-cancelled. Must stay byte-identical to the platform's
     * {@code CANCEL_RPC_MSG}.
     */
    public static final String CANCEL_SENTINEL = "__ff_job_cancelled__";

    private Protocol() {}
}
