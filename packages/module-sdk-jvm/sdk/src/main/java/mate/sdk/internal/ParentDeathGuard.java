package mate.sdk.internal;

import java.util.Optional;

/**
 * A worker must never outlive the platform (modules/PROTOCOL.md §2). The
 * platform SIGKILLs the worker's process group on shutdown and hard-cancel,
 * but a hard host death (SIGKILL/OOM) can't do that - so the worker polls its
 * parent and halts the JVM the moment it is gone or the pid reparented.
 * {@code halt} (not {@code exit}): no shutdown hooks, no finalizers - the
 * mirror of the Python worker's {@code os._exit(137)}.
 */
public final class ParentDeathGuard {

    public static void install() {
        long originalParent =
                ProcessHandle.current().parent().map(ProcessHandle::pid).orElse(-1L);
        Thread guard =
                new Thread(
                        () -> {
                            while (true) {
                                try {
                                    Thread.sleep(500);
                                } catch (InterruptedException exc) {
                                    return;
                                }
                                Optional<ProcessHandle> parent = ProcessHandle.current().parent();
                                boolean gone =
                                        parent.isEmpty()
                                                || !parent.get().isAlive()
                                                || (originalParent > 0
                                                        && parent.get().pid() != originalParent);
                                if (gone) {
                                    Runtime.getRuntime().halt(137);
                                }
                            }
                        },
                        "mate-parent-death-guard");
        guard.setDaemon(true);
        guard.start();
    }

    private ParentDeathGuard() {}
}
