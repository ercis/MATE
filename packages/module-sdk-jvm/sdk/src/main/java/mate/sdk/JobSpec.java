package mate.sdk;

/**
 * Marks a handler as a platform job: stacked on a route, the route enqueues
 * and immediately returns {@code {"job_id": ...}}; stacked on an event
 * subscription, each event runs a job (the precompute pattern).
 *
 * <p>Immutable; every wither returns a new spec. Titles/subtitles are static
 * strings (dynamic per-payload labels cannot cross the worker socket).
 */
public final class JobSpec {

    private final boolean progress;
    private final String title;
    private final String subtitle;
    private final int priority;
    private final boolean cancellable;
    private final String resultUrl;

    private JobSpec(
            boolean progress,
            String title,
            String subtitle,
            int priority,
            boolean cancellable,
            String resultUrl) {
        this.progress = progress;
        this.title = title;
        this.subtitle = subtitle;
        this.priority = priority;
        this.cancellable = cancellable;
        this.resultUrl = resultUrl;
    }

    public static JobSpec of() {
        return new JobSpec(false, null, null, 0, true, null);
    }

    /** The handler reports progress via {@link Progress#update} - the UI shows a real bar. */
    public JobSpec progress(boolean progress) {
        return new JobSpec(progress, title, subtitle, priority, cancellable, resultUrl);
    }

    public JobSpec title(String title) {
        return new JobSpec(progress, title, subtitle, priority, cancellable, resultUrl);
    }

    public JobSpec subtitle(String subtitle) {
        return new JobSpec(progress, title, subtitle, priority, cancellable, resultUrl);
    }

    public JobSpec priority(int priority) {
        return new JobSpec(progress, title, subtitle, priority, cancellable, resultUrl);
    }

    public JobSpec cancellable(boolean cancellable) {
        return new JobSpec(progress, title, subtitle, priority, cancellable, resultUrl);
    }

    /** Route path the finished job's UI entry links to (e.g. a results page). */
    public JobSpec resultUrl(String resultUrl) {
        return new JobSpec(progress, title, subtitle, priority, cancellable, resultUrl);
    }

    public boolean isProgress() {
        return progress;
    }

    public String getTitle() {
        return title;
    }

    public String getSubtitle() {
        return subtitle;
    }

    public int getPriority() {
        return priority;
    }

    public boolean isCancellable() {
        return cancellable;
    }

    public String getResultUrl() {
        return resultUrl;
    }
}
