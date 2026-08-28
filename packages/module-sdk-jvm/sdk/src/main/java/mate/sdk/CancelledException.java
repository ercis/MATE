package mate.sdk;

/**
 * Raised when the platform has cancelled the running job: every {@code ctx.*}
 * call of a soft-cancelled job fails with this, and {@link ModuleContext#checkCancelled()}
 * throws it explicitly.
 *
 * <p>Deliberately extends {@link Error}, not {@link Exception}: wrapped research
 * code full of broad {@code catch (Exception e)} blocks must not be able to
 * swallow a cancellation into a "failed" job. Let it propagate - the SDK turns
 * it into the protocol's cancel signal so the platform records the job as
 * {@code cancelled}. (Mirror of the Python SDK's {@code Cancelled(BaseException)}.)
 */
public final class CancelledException extends Error {

    public CancelledException() {
        super("job cancelled by the platform");
    }
}
