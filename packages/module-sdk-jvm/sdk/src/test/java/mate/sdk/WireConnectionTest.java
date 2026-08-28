package mate.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.channels.Channels;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import mate.sdk.internal.WireConnection;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Exercises the worker-side wire endpoint against a raw fake host over a real
 * Unix socketpair: framing, id bookkeeping, cancel-sentinel mapping, error
 * shape, and notification handling.
 */
class WireConnectionTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final TypeReference<Map<String, Object>> OBJ =
            new TypeReference<Map<String, Object>>() {};

    private Path socketDir;
    private ServerSocketChannel server;
    private SocketChannel hostSide;
    private WireConnection worker;
    private BufferedReader hostReader;
    private BufferedWriter hostWriter;
    private Thread workerLoop;

    @BeforeEach
    void setUp() throws Exception {
        socketDir = Files.createTempDirectory("mate-sdk-test");
        Path socket = socketDir.resolve("rpc.sock");
        server = ServerSocketChannel.open(StandardProtocolFamily.UNIX);
        server.bind(UnixDomainSocketAddress.of(socket));

        SocketChannel workerChannel = SocketChannel.open(StandardProtocolFamily.UNIX);
        workerChannel.connect(UnixDomainSocketAddress.of(socket));
        hostSide = server.accept();

        worker = WireConnection.wrap(workerChannel);
        hostReader =
                new BufferedReader(
                        new InputStreamReader(Channels.newInputStream(hostSide), StandardCharsets.UTF_8));
        hostWriter =
                new BufferedWriter(
                        new OutputStreamWriter(Channels.newOutputStream(hostSide), StandardCharsets.UTF_8));
        workerLoop = new Thread(worker::runLoop, "worker-loop");
        workerLoop.setDaemon(true);
        workerLoop.start();
    }

    @AfterEach
    void tearDown() throws Exception {
        hostSide.close();
        server.close();
        worker.close();
        workerLoop.join(2000);
    }

    private Map<String, Object> hostRead() throws IOException {
        String line = hostReader.readLine();
        assertNotNull(line, "expected a frame from the worker");
        return MAPPER.readValue(line, OBJ);
    }

    private void hostWrite(Map<String, Object> msg) throws IOException {
        hostWriter.write(MAPPER.writeValueAsString(msg));
        hostWriter.write("\n");
        hostWriter.flush();
    }

    @Test
    void requestRoundTripMatchesById() throws Exception {
        CompletableFuture<Object> result =
                CompletableFuture.supplyAsync(() -> worker.request("ctx.cache.exists", new HashMap<>()));
        Map<String, Object> frame = hostRead();
        assertEquals("ctx.cache.exists", frame.get("method"));
        Number id = (Number) frame.get("id");
        // Reply to an unrelated id first - it must be dropped, not matched.
        hostWrite(Map.of("id", 9999, "result", false));
        hostWrite(Map.of("id", id, "result", true));
        assertEquals(true, result.get(2, TimeUnit.SECONDS));
    }

    @Test
    void cancelSentinelBecomesCancelledException() throws Exception {
        CompletableFuture<Throwable> thrown =
                CompletableFuture.supplyAsync(
                        () -> {
                            try {
                                worker.request("ctx.cancel.check", new HashMap<>());
                                return null;
                            } catch (Throwable t) {
                                return t;
                            }
                        });
        Map<String, Object> frame = hostRead();
        hostWrite(
                Map.of(
                        "id",
                        frame.get("id"),
                        "error",
                        Map.of("message", "RuntimeError: __ff_job_cancelled__")));
        assertTrue(thrown.get(2, TimeUnit.SECONDS) instanceof CancelledException);
    }

    @Test
    void otherErrorsBecomeRpcException() throws Exception {
        CompletableFuture<Throwable> thrown =
                CompletableFuture.supplyAsync(
                        () -> {
                            try {
                                worker.request("ctx.bus.emit", new HashMap<>());
                                return null;
                            } catch (Throwable t) {
                                return t;
                            }
                        });
        Map<String, Object> frame = hostRead();
        hostWrite(Map.of("id", frame.get("id"), "error", Map.of("message", "ValueError: nope")));
        Throwable t = thrown.get(2, TimeUnit.SECONDS);
        assertTrue(t instanceof RpcException);
        assertTrue(t.getMessage().contains("nope"));
    }

    @Test
    void inboundDispatchRepliesWithResultAndErrors() throws Exception {
        worker.register("ping", params -> true);
        worker.register(
                "explode",
                params -> {
                    throw new IllegalStateException("boom");
                });
        worker.register(
                "cancelled",
                params -> {
                    throw new CancelledException();
                });

        hostWrite(Map.of("id", 1, "method", "ping", "params", Map.of()));
        Map<String, Object> pong = hostRead();
        assertEquals(1, ((Number) pong.get("id")).intValue());
        assertEquals(true, pong.get("result"));

        hostWrite(Map.of("id", 2, "method", "explode", "params", Map.of()));
        Map<String, Object> error = hostRead();
        @SuppressWarnings("unchecked")
        Map<String, Object> errObj = (Map<String, Object>) error.get("error");
        assertTrue(String.valueOf(errObj.get("message")).contains("boom"));
        assertNotNull(errObj.get("traceback"));

        hostWrite(Map.of("id", 3, "method", "cancelled", "params", Map.of()));
        Map<String, Object> cancelled = hostRead();
        @SuppressWarnings("unchecked")
        Map<String, Object> cancelObj = (Map<String, Object>) cancelled.get("error");
        assertEquals("__ff_job_cancelled__", cancelObj.get("message"));

        hostWrite(Map.of("id", 4, "method", "no_such_method", "params", Map.of()));
        Map<String, Object> unknown = hostRead();
        @SuppressWarnings("unchecked")
        Map<String, Object> unknownObj = (Map<String, Object>) unknown.get("error");
        assertTrue(String.valueOf(unknownObj.get("message")).contains("unknown method"));
    }

    @Test
    void notificationCarriesNullId() throws Exception {
        worker.notify("ready", Map.of("protocol", 1));
        Map<String, Object> frame = hostRead();
        assertNull(frame.get("id"));
        assertEquals("ready", frame.get("method"));
    }

    @Test
    void overlappingInboundCallsBothComplete() throws Exception {
        CountDownLatch firstStarted = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        worker.register(
                "slow",
                params -> {
                    firstStarted.countDown();
                    release.await(2, TimeUnit.SECONDS);
                    return "slow-done";
                });
        worker.register("fast", params -> "fast-done");

        hostWrite(Map.of("id", 10, "method", "slow", "params", Map.of()));
        assertTrue(firstStarted.await(2, TimeUnit.SECONDS));
        hostWrite(Map.of("id", 11, "method", "fast", "params", Map.of()));

        // The fast call must complete while the slow one is still blocked.
        Map<String, Object> fast = hostRead();
        assertEquals(11, ((Number) fast.get("id")).intValue());
        assertEquals("fast-done", fast.get("result"));

        release.countDown();
        Map<String, Object> slow = hostRead();
        assertEquals(10, ((Number) slow.get("id")).intValue());
        assertEquals("slow-done", slow.get("result"));
    }
}
