"""Streamlined CV4CDD WINSIM detection pipeline.

Adapted from the CV4CDD-4D reference implementation by A. Kraus and
H. van der Aa (University of Mannheim, BPM'24). The original repository
in ``modules/cv4cdd/approaches/object_detection`` is kept for reference;
this file is the self-contained version used by the platform.

Differences from the upstream code:
- No dependency on tf-models-official: the only function we need from it
  is a resize-and-pad which we reimplement with tf.image primitives.
- No filesystem walking: the entry point takes an already-loaded DataFrame
  and returns the detections + a PNG with bounding-box overlays in-memory.
- Single-log scope: there is no train/eval batch concept here; each call
  encodes one event log into one similarity-matrix image.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

# WINSIM model expects 256×256 inputs; the four drift classes are fixed.
TARGETSIZE = 256
INPUT_SIZE = (256, 256)

CATEGORY_INDEX = {
    1: {"name": "sudden",      "color": (255, 255, 255)},  # white
    2: {"name": "gradual",     "color": (30, 144, 255)},   # dodgerblue
    3: {"name": "incremental", "color": (255, 0, 255)},    # magenta
    4: {"name": "recurring",   "color": (0, 255, 255)},    # aqua
}


# ---------------------------------------------------------------------------
# WINSIM encoding
# ---------------------------------------------------------------------------


def log_to_windowed_dfg_count(
    df: pd.DataFrame, n_windows: int
) -> tuple[np.ndarray, dict[int, tuple[str, str]]]:
    """Slice the log into N windows of equal trace count and return one
    activity-pair frequency matrix per window plus a ``window → (first
    trace id, first timestamp)`` lookup used to translate detection
    bounding boxes back into wall-clock time.
    """
    from pm4py import discover_dfg_typed  # inherited dep - always available

    activities = sorted(df["activity"].astype(str).unique())
    act_idx = {a: i for i, a in enumerate(activities)}
    n_acts = len(activities)

    # Sort events by timestamp to get traces in chronological order,
    # mirroring pm4py's TIMESTAMP_SORT behaviour in the reference repo.
    # Must use mergesort (stable) - quicksort (pandas default) breaks ties
    # arbitrarily, producing a different trace order from the reference for
    # the ~17 traces that all start at 2017-01-01 00:00:00.
    df_ts = df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    unique_traces = list(pd.unique(df_ts["case_id"]))

    window_size = max(1, len(unique_traces) // n_windows)

    dfg_arrays: list[np.ndarray] = []
    window_information: dict[int, tuple[str, str]] = {}

    for i in range(n_windows):
        left = i * window_size
        right = (i + 1) * window_size if i < n_windows - 1 else len(unique_traces)

        if left >= len(unique_traces):
            dfg_arrays.append(np.zeros((n_acts, n_acts), dtype=np.float32))
            if window_information:
                window_information[i + 1] = window_information[i]
            continue

        traces_in_window = unique_traces[left:right]
        window_df = df_ts[df_ts["case_id"].isin(traces_in_window)].rename(
            columns={
                "case_id": "case:concept:name",
                "activity": "concept:name",
                "timestamp": "time:timestamp",
            }
        )

        # Use pm4py's DFG discovery to match the reference implementation
        # exactly, including its tie-breaking order for events with the same
        # timestamp within a case.
        graph, _, _ = discover_dfg_typed(window_df)

        dfg = np.zeros((n_acts, n_acts), dtype=np.float32)
        for (a, b), freq in graph.items():
            sa, sb = str(a), str(b)
            if sa in act_idx and sb in act_idx:
                dfg[act_idx[sa], act_idx[sb]] = float(freq)
        dfg_arrays.append(dfg)

        first_trace = traces_in_window[0]
        ft = df.loc[df["case_id"] == first_trace, "timestamp"].min()
        window_information[i + 1] = (
            str(first_trace),
            pd.Timestamp(ft).strftime("%Y-%m-%dT%H:%M:%S"),
        )

    return np.array(dfg_arrays), window_information


def similarity_calculation(windowed_dfg: np.ndarray) -> np.ndarray:
    """Compute pairwise cosine-distance between window DFG matrices and
    return a uint8 similarity image (255 = identical, 0 = max distance).

    Vectorised cosine distance - `1 - (X @ X.T) / (||X|| * ||X||.T)` - so
    we don't need scipy here. The compute is O(n²·d) regardless.
    """
    n = len(windowed_dfg)
    flat = windowed_dfg.reshape(n, -1).astype(np.float64)
    norms = np.linalg.norm(flat, axis=1)
    # Avoid division by zero for empty windows: a row with zero norm yields
    # zero similarity with every other row (distance = 0 → max similarity).
    safe = np.where(norms > 0, norms, 1.0)
    cos = (flat @ flat.T) / np.outer(safe, safe)
    cos = np.clip(cos, -1.0, 1.0)
    sim = 1.0 - cos
    np.fill_diagonal(sim, 0.0)

    max_val = sim.max() or 1.0
    norm = 1.0 - sim / max_val
    return np.uint8(norm * 255)


def _viridis_lut() -> np.ndarray:
    """Exact 256-entry RGB table for matplotlib's viridis colormap.

    Obtained via ``(plt.get_cmap('viridis')(np.linspace(0,1,256))[:,:3]*255)
    .astype(np.uint8)``.  Using ``np.linspace`` + truncation (not rounding)
    replicates ``(cm(matrix)[:,:,:3]*255).astype(np.uint8)`` with max diff=0,
    which is critical: even a 1-pixel deviation shifts detection boxes by
    10+ windows on the InternationalDeclarations benchmark log.
    """
    data = np.array([
        (68,1,84),(68,2,85),(68,3,87),(69,5,88),(69,6,90),(69,8,91),(70,9,92),
        (70,11,94),(70,12,95),(70,14,97),(71,15,98),(71,17,99),(71,18,101),
        (71,20,102),(71,21,103),(71,22,105),(71,24,106),(72,25,107),(72,26,108),
        (72,28,110),(72,29,111),(72,30,112),(72,32,113),(72,33,114),(72,34,115),
        (72,35,116),(71,37,117),(71,38,118),(71,39,119),(71,40,120),(71,42,121),
        (71,43,122),(71,44,123),(70,45,124),(70,47,124),(70,48,125),(70,49,126),
        (69,50,127),(69,52,127),(69,53,128),(69,54,129),(68,55,129),(68,57,130),
        (67,58,131),(67,59,131),(67,60,132),(66,61,132),(66,62,133),(66,64,133),
        (65,65,134),(65,66,134),(64,67,135),(64,68,135),(63,69,135),(63,71,136),
        (62,72,136),(62,73,137),(61,74,137),(61,75,137),(61,76,137),(60,77,138),
        (60,78,138),(59,80,138),(59,81,138),(58,82,139),(58,83,139),(57,84,139),
        (57,85,139),(56,86,139),(56,87,140),(55,88,140),(55,89,140),(54,90,140),
        (54,91,140),(53,92,140),(53,93,140),(52,94,141),(52,95,141),(51,96,141),
        (51,97,141),(50,98,141),(50,99,141),(49,100,141),(49,101,141),(49,102,141),
        (48,103,141),(48,104,141),(47,105,141),(47,106,141),(46,107,142),(46,108,142),
        (46,109,142),(45,110,142),(45,111,142),(44,112,142),(44,113,142),(44,114,142),
        (43,115,142),(43,116,142),(42,117,142),(42,118,142),(42,119,142),(41,120,142),
        (41,121,142),(40,122,142),(40,122,142),(40,123,142),(39,124,142),(39,125,142),
        (39,126,142),(38,127,142),(38,128,142),(38,129,142),(37,130,142),(37,131,141),
        (36,132,141),(36,133,141),(36,134,141),(35,135,141),(35,136,141),(35,137,141),
        (34,137,141),(34,138,141),(34,139,141),(33,140,141),(33,141,140),(33,142,140),
        (32,143,140),(32,144,140),(32,145,140),(31,146,140),(31,147,139),(31,148,139),
        (31,149,139),(31,150,139),(30,151,138),(30,152,138),(30,153,138),(30,153,138),
        (30,154,137),(30,155,137),(30,156,137),(30,157,136),(30,158,136),(30,159,136),
        (30,160,135),(31,161,135),(31,162,134),(31,163,134),(32,164,133),(32,165,133),
        (33,166,133),(33,167,132),(34,167,132),(35,168,131),(35,169,130),(36,170,130),
        (37,171,129),(38,172,129),(39,173,128),(40,174,127),(41,175,127),(42,176,126),
        (43,177,125),(44,177,125),(46,178,124),(47,179,123),(48,180,122),(50,181,122),
        (51,182,121),(53,183,120),(54,184,119),(56,185,118),(57,185,118),(59,186,117),
        (61,187,116),(62,188,115),(64,189,114),(66,190,113),(68,190,112),(69,191,111),
        (71,192,110),(73,193,109),(75,194,108),(77,194,107),(79,195,105),(81,196,104),
        (83,197,103),(85,198,102),(87,198,101),(89,199,100),(91,200,98),(94,201,97),
        (96,201,96),(98,202,95),(100,203,93),(103,204,92),(105,204,91),(107,205,89),
        (109,206,88),(112,206,86),(114,207,85),(116,208,84),(119,208,82),(121,209,81),
        (124,210,79),(126,210,78),(129,211,76),(131,211,75),(134,212,73),(136,213,71),
        (139,213,70),(141,214,68),(144,214,67),(146,215,65),(149,215,63),(151,216,62),
        (154,216,60),(157,217,58),(159,217,56),(162,218,55),(165,218,53),(167,219,51),
        (170,219,50),(173,220,48),(175,220,46),(178,221,44),(181,221,43),(183,221,41),
        (186,222,39),(189,222,38),(191,223,36),(194,223,34),(197,223,33),(199,224,31),
        (202,224,30),(205,224,29),(207,225,28),(210,225,27),(212,225,26),(215,226,25),
        (218,226,24),(220,226,24),(223,227,24),(225,227,24),(228,227,24),(231,228,25),
        (233,228,25),(236,228,26),(238,229,27),(241,229,28),(243,229,30),(246,230,31),
        (248,230,33),(250,230,34),(253,231,36),
    ], dtype=np.uint8)
    return data


_VIRIDIS = _viridis_lut()


def matrix_to_image_bytes(matrix: np.ndarray) -> bytes:
    """Apply the viridis colormap via LUT and return PNG bytes at INPUT_SIZE for display."""
    coloured = _VIRIDIS[matrix]  # (H, W, 3) uint8
    img = Image.fromarray(coloured).resize(INPUT_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _matrix_to_model_jpeg(matrix: np.ndarray) -> bytes:
    """Encode the similarity matrix as a JPEG at its natural resolution.

    The SavedModel was trained on PIL-decoded 200×200 JPEG images (quality 75,
    PIL default).  Saving at the native n_windows×n_windows size and decoding
    with PIL before resizing exactly replicates the reference pipeline
    (utils.matrix_to_img → utils.load_image → build_inputs_for_object_detection).
    Using PNG or pre-resizing to 256×256 changes the pixel statistics enough
    that some drift classes fall below the 0.5 detection threshold.
    """
    coloured = _VIRIDIS[matrix]  # (H, W, 3) uint8  - natural matrix size
    buf = io.BytesIO()
    Image.fromarray(coloured).save(buf, format="JPEG")  # default quality=75
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Model inference
# ---------------------------------------------------------------------------


def _resize_for_model(jpeg_bytes: bytes) -> "Any":  # noqa: F821 - tf is imported lazily
    """PIL-decode JPEG → TF bilinear resize → uint8 batch tensor (1, 256, 256, 3).

    Matches the reference repo exactly:
      utils.load_image  →  np.array(Image.open(path))          (PIL decode)
      build_inputs_for_object_detection  →  resize_and_crop_image  (TF bilinear)
    Using tf.io.decode_jpeg instead of PIL gives different DCT rounding and
    shifts detection boxes by 1-15 windows.
    """
    import tensorflow as tf

    img_np = np.array(Image.open(io.BytesIO(jpeg_bytes)).convert("RGB"))
    image = tf.constant(img_np, dtype=tf.float32)
    image = tf.image.resize(image, INPUT_SIZE, method=tf.image.ResizeMethod.BILINEAR)
    image = tf.cast(image, tf.uint8)
    return tf.expand_dims(image, axis=0)


def predict_drifts(
    model: Any,
    image_bytes: bytes,
    n_windows: int,
    threshold: float,
    window_information: dict[int, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Run the saved-model on the similarity image and decode bounding
    boxes (pixel coords) into wall-clock drift intervals.
    """
    batch = _resize_for_model(image_bytes)
    result = model.signatures["serving_default"](batch)

    scores = result["detection_scores"][0].numpy()
    boxes = result["detection_boxes"][0].numpy()
    classes = result["detection_classes"][0].numpy().astype(int)

    keep = scores > threshold
    scores = scores[keep]
    boxes = boxes[keep]
    classes = classes[keep]

    def to_window(pixel: float) -> int:
        return max(1, min(n_windows, int(round(pixel / TARGETSIZE * n_windows))))

    drifts: list[dict[str, Any]] = []
    for box, cls, score in zip(boxes, classes, scores):
        ymin, xmin, ymax, xmax = (float(v) for v in box)
        cls_name = CATEGORY_INDEX.get(int(cls), {}).get("name", "unknown")

        # The reference repo (get_changepoints_trace_idx_winsim) uses the y-axis
        # (row coordinates) of the bounding box to locate drift windows, because
        # detection_boxes is [ymin, xmin, ymax, xmax] and the original code indexes
        # into bbox[0]=ymin and bbox[2]=ymax after scaling.  For sudden drifts it
        # adds RESIZE_VALUE=5 to ymin_window (compensating for the training-time
        # bbox pre-processing) rather than using the box centre.
        if cls_name == "sudden":
            ymin_w = to_window(ymin)
            if ymin_w == 0:
                w = 2  # = RESIZE_VALUE // 2
            elif ymin_w + 5 == n_windows:
                w = n_windows - 2
            else:
                w = min(n_windows, ymin_w + 5)
            start_ts = window_information.get(w, ("", ""))[1]
            end_ts = start_ts
            start_w = end_w = w
        else:
            start_w = max(1, to_window(ymin))
            end_w = min(n_windows, to_window(ymax))
            start_ts = window_information.get(start_w, ("", ""))[1]
            end_ts = window_information.get(end_w, ("", ""))[1]

        drifts.append(
            {
                "type": cls_name,
                "start_timestamp": start_ts,
                "end_timestamp": end_ts,
                "start_window": start_w,
                "end_window": end_w,
                "confidence": float(score),
                "bbox": [xmin, ymin, xmax, ymax],
            }
        )

    # Sort chronologically so the panel table reads naturally.
    drifts.sort(key=lambda d: (d["start_timestamp"], -d["confidence"]))
    return drifts


# ---------------------------------------------------------------------------
# Overlay rendering
# ---------------------------------------------------------------------------


def render_overlay(image_bytes: bytes, drifts: list[dict[str, Any]]) -> bytes:
    """Draw bounding boxes + labels on the similarity image; return PNG bytes."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    scale = 3  # render at 768×768 so the labels stay crisp
    img = img.resize((INPUT_SIZE[0] * scale, INPUT_SIZE[1] * scale), Image.LANCZOS)
    draw = ImageDraw.Draw(img, mode="RGBA")

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except OSError:
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18
            )
        except OSError:
            font = ImageFont.load_default()

    type_to_color = {info["name"]: info["color"] for info in CATEGORY_INDEX.values()}

    for d in drifts:
        xmin, ymin, xmax, ymax = (v * scale for v in d["bbox"])
        colour = type_to_color.get(d["type"], (255, 255, 0))

        draw.rectangle([xmin, ymin, xmax, ymax], outline=colour, width=3)

        label = f'{d["type"]}: {int(d["confidence"] * 100)}%'
        bbox = draw.textbbox((xmin, max(0, ymin - 22)), label, font=font)
        # Translucent background behind the label so it's readable on any colour
        pad = 2
        draw.rectangle(
            [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
            fill=(*colour, 220),
        )
        draw.text((xmin, max(0, ymin - 22)), label, fill=(0, 0, 0), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Model cache - loaded once per process, reused across all detection runs.
# ---------------------------------------------------------------------------

_MODEL_CACHE: dict[str, Any] = {}

# Detection runs inside `asyncio.to_thread` workers. Two concurrent cv4cdd jobs
# (e.g. two logs imported close together) would otherwise import TensorFlow from
# two worker threads at once - a first-time cross-thread import that can trip
# CPython's import-lock deadlock detector. Serialise the import so only one
# thread ever loads it; once it's in sys.modules the lock is effectively free.
_TF_IMPORT_LOCK = threading.Lock()


def _get_model(model_path: Path) -> Any:
    with _TF_IMPORT_LOCK:
        import tensorflow as tf  # lazy - only loaded on actual run

    key = str(model_path)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = tf.saved_model.load(key)
    return _MODEL_CACHE[key]


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def run_detection(
    df: pd.DataFrame,
    model_path: Path,
    n_windows: int = 200,
    threshold: float = 0.5,
    progress: Any = None,
) -> dict[str, Any]:
    """End-to-end WINSIM detection.

    Args:
        df: Event log dataframe with columns ``case_id``, ``activity``, ``timestamp``.
        model_path: Path to the TensorFlow SavedModel directory.
        n_windows: Number of WINSIM windows.
        threshold: Confidence threshold for kept detections.
        progress: Optional callable ``(fraction, message)`` for progress reporting.

    Returns:
        ``{"drifts": [...], "similarity_png": bytes, "overlay_png": bytes,
           "n_windows": int}``.
    """
    def _emit(p: float, msg: str) -> None:
        if progress is not None:
            try:
                progress(p, msg)
            except Exception:  # noqa: BLE001 - progress is best-effort
                pass

    _emit(0.15, "Windowing the event log")
    windowed_dfg, window_information = log_to_windowed_dfg_count(df, n_windows)

    _emit(0.40, "Computing similarity matrix")
    sim_matrix = similarity_calculation(windowed_dfg)

    _emit(0.55, "Encoding image")
    image_bytes = matrix_to_image_bytes(sim_matrix)   # PNG for display/overlay
    model_jpeg = _matrix_to_model_jpeg(sim_matrix)    # JPEG for model inference

    _emit(0.65, "Loading model")
    model = _get_model(model_path)

    _emit(0.85, "Running drift detection")
    drifts = predict_drifts(model, model_jpeg, n_windows, threshold, window_information)

    _emit(0.95, "Rendering overlay")
    overlay_png = render_overlay(image_bytes, drifts)

    return {
        "drifts": drifts,
        "similarity_png": image_bytes,
        "overlay_png": overlay_png,
        "n_windows": n_windows,
    }
