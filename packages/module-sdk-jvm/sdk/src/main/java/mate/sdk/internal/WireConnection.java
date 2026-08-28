package mate.sdk.internal;

import java.io.BufferedOutputStream;
import java.io.BufferedReader;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.channels.Channels;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Function;
import mate.sdk.CancelledException;
import mate.sdk.RpcException;

/**
 * Bidirectional newline-delimited JSON-RPC over a Unix stream socket - the
 * worker side of modules/PROTOCOL.md §3.
 *
 * <p>One reader loop (run on the caller of {@link #runLoop()}), one write lock
 * (frames never interleave), independent id spaces per direction, and a cached
 * daemon-thread pool dispatching inbound requests so overlapping {@code call}s
 * and mid-call {@code ctx.*} traffic never deadlock.
 */
public final class WireConnection implements RpcTransport, Closeable {

    /** Inbound request handler: params in, JSON-representable result out. */
    @FunctionalInterface
    public interface MethodHandler {
        Object apply(Map<String, Object> params) throws Exception;
    }

    private final SocketChannel channel;
    private final BufferedReader reader;
    private final OutputStream out;
    private final Object writeLock = new Object();
    private final AtomicLong nextId = new AtomicLong(1);
    private final ConcurrentHashMap<Long, CompletableFuture<Object>> pending =
            new ConcurrentHashMap<>();
    private final Map<String, MethodHandler> dispatcher = new ConcurrentHashMap<>();
    private final ExecutorService dispatchPool;

    private WireConnection(SocketChannel channel) {
        this.channel = channel;
        this.reader =
                new BufferedReader(
                        new InputStreamReader(Channels.newInputStream(channel), StandardCharsets.UTF_8),
                        1 << 16);
        this.out = new BufferedOutputStream(Channels.newOutputStream(channel), 1 << 16);
        this.dispatchPool =
                Executors.newCachedThreadPool(
                        r -> {
                            Thread t = new Thread(r, "mate-sdk-dispatch");
                            t.setDaemon(true);
                            return t;
                        });
    }

    public static WireConnection connect(Path socketPath) throws IOException {
        SocketChannel channel = SocketChannel.open(StandardProtocolFamily.UNIX);
        channel.connect(UnixDomainSocketAddress.of(socketPath));
        return new WireConnection(channel);
    }

    /** Test seam: wrap an already-connected channel (e.g. a socketpair end). */
    public static WireConnection wrap(SocketChannel connected) {
        return new WireConnection(connected);
    }

    public void register(String method, MethodHandler handler) {
        dispatcher.put(method, handler);
    }

    /** Send the {@code ready} notification ({@code id: null}). */
    public void notify(String method, Map<String, Object> params) {
        Map<String, Object> msg = new HashMap<>();
        msg.put("id", null);
        msg.put("method", method);
        msg.put("params", params);
        writeFrame(msg);
    }

    @Override
    public Object request(String method, Map<String, Object> params) {
        CompletableFuture<Object> future = send(method, params);
        try {
            return future.get();
        } catch (ExecutionException exc) {
            Throwable cause = exc.getCause();
            if (cause instanceof CancelledException cancelled) {
                throw cancelled;
            }
            if (cause instanceof RpcException rpc) {
                throw rpc;
            }
            throw new RpcException(String.valueOf(cause), cause);
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            throw new RpcException("interrupted while waiting for " + method, exc);
        }
    }

    @Override
    public void requestFireAndForget(String method, Map<String, Object> params) {
        // The reply still resolves (and removes) the pending future - it is
        // simply never awaited.
        send(method, params);
    }

    private CompletableFuture<Object> send(String method, Map<String, Object> params) {
        long id = nextId.getAndIncrement();
        CompletableFuture<Object> future = new CompletableFuture<>();
        pending.put(id, future);
        Map<String, Object> msg = new HashMap<>();
        msg.put("id", id);
        msg.put("method", method);
        msg.put("params", params);
        try {
            writeFrame(msg);
        } catch (RuntimeException exc) {
            pending.remove(id);
            future.completeExceptionally(new RpcException("connection write failed", exc));
        }
        return future;
    }

    private void writeFrame(Map<String, Object> msg) {
        byte[] bytes = (Jsons.write(msg) + "\n").getBytes(StandardCharsets.UTF_8);
        synchronized (writeLock) {
            try {
                out.write(bytes);
                out.flush();
            } catch (IOException exc) {
                throw new RpcException("connection to the platform is gone", exc);
            }
        }
    }

    /**
     * Read frames until EOF. Run this on the worker's main thread - when it
     * returns, the platform hung up and the worker should exit.
     */
    public void runLoop() {
        try {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isEmpty()) {
                    continue;
                }
                Map<String, Object> msg;
                try {
                    msg = Jsons.parseObject(line);
                } catch (RuntimeException exc) {
                    continue; // skip malformed frames, mirroring the reference worker
                }
                if (msg.containsKey("method")) {
                    dispatchPool.submit(() -> dispatch(msg));
                } else {
                    resolve(msg);
                }
            }
        } catch (IOException ignored) {
            // treat any read failure as EOF
        } finally {
            failAllPending(new RpcException("connection to the platform closed"));
        }
    }

    private void resolve(Map<String, Object> msg) {
        Object rawId = msg.get("id");
        if (!(rawId instanceof Number number)) {
            return; // response to a notification (id null) - drop
        }
        CompletableFuture<Object> future = pending.remove(number.longValue());
        if (future == null || future.isDone()) {
            return;
        }
        Object error = msg.get("error");
        if (error instanceof Map<?, ?> errorMap) {
            String message = String.valueOf(errorMap.get("message"));
            if (message.contains(Protocol.CANCEL_SENTINEL)) {
                future.completeExceptionally(new CancelledException());
            } else {
                future.completeExceptionally(new RpcException(message));
            }
        } else {
            future.complete(msg.get("result"));
        }
    }

    private void dispatch(Map<String, Object> msg) {
        Object rid = msg.get("id");
        String method = String.valueOf(msg.get("method"));
        @SuppressWarnings("unchecked")
        Map<String, Object> params =
                msg.get("params") instanceof Map ? (Map<String, Object>) msg.get("params") : Map.of();
        MethodHandler handler = dispatcher.get(method);
        Map<String, Object> response = new HashMap<>();
        response.put("id", rid);
        if (handler == null) {
            response.put("error", Map.of("message", "unknown method '" + method + "'"));
            safeWrite(response);
            return;
        }
        try {
            response.put("result", handler.apply(params));
        } catch (CancelledException cancelled) {
            // Report the sentinel so the platform records `cancelled`, not `failed`.
            response.put("error", Map.of("message", Protocol.CANCEL_SENTINEL));
        } catch (Throwable exc) {
            Map<String, Object> error = new HashMap<>();
            error.put("message", exc.getClass().getSimpleName() + ": " + exc.getMessage());
            error.put("traceback", stackTrace(exc));
            response.put("error", error);
        }
        safeWrite(response);
    }

    private void safeWrite(Map<String, Object> response) {
        try {
            writeFrame(response);
        } catch (RuntimeException ignored) {
            // connection is gone; runLoop's EOF handling ends the worker
        }
    }

    private void failAllPending(RpcException exc) {
        for (CompletableFuture<Object> future : pending.values()) {
            future.completeExceptionally(exc);
        }
        pending.clear();
    }

    private static String stackTrace(Throwable exc) {
        java.io.StringWriter writer = new java.io.StringWriter();
        exc.printStackTrace(new java.io.PrintWriter(writer));
        return writer.toString();
    }

    @Override
    public void close() throws IOException {
        channel.close();
    }
}
