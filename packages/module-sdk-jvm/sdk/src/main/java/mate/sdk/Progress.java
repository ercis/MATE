package mate.sdk;

/**
 * Live progress reporting for jobs declared with {@code JobSpec.progress(true)}.
 * {@code current} alone may be a fraction in {@code [0,1]} or a running
 * counter; pair it with {@code total} for absolute progress. Long jobs should
 * tick at least every couple of minutes or the UI shows a stall hint.
 *
 * <p>Every update is also a cooperative cancellation poll point: it throws
 * {@link CancelledException} once the job is cancelled.
 */
public interface Progress {

    void update(double current);

    void update(double current, String message);

    void update(double current, Double total, String stage, String message);
}
