package mate.sdk.internal;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.UncheckedIOException;
import java.util.Map;

/**
 * The SDK's single Jackson entry point. Jackson is an internal, relocated
 * dependency (`mate.sdk.internal.jackson` in the shipped jar) - nothing
 * Jackson-typed appears on the public SDK surface; everything is plain
 * Map/List/String/Number/Boolean.
 */
public final class Jsons {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final TypeReference<Map<String, Object>> OBJECT =
            new TypeReference<Map<String, Object>>() {};

    public static String write(Object value) {
        try {
            return MAPPER.writeValueAsString(value);
        } catch (Exception exc) {
            throw new UncheckedIOException(
                    new java.io.IOException("value is not JSON-serializable: " + exc.getMessage(), exc));
        }
    }

    public static Map<String, Object> parseObject(String line) {
        try {
            return MAPPER.readValue(line, OBJECT);
        } catch (Exception exc) {
            throw new UncheckedIOException(
                    new java.io.IOException("malformed JSON frame: " + exc.getMessage(), exc));
        }
    }

    private Jsons() {}
}
