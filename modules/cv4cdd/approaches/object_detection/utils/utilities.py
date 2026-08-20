import os
import json
import datetime as dt
import pytz
import pandas as pd
import tensorflow as tf
import matplotlib.pyplot as plt
import numpy as np

from PIL import Image
from typing import Union, Tuple
from pm4py.objects.log.obj import EventLog
from pm4py.objects.log.importer.xes import importer as xes_importer
from pm4py.algo.filtering.log.attributes import attributes_filter
from pm4py import discover_dfg_typed
from official.vision.dataloaders.tf_example_decoder import TfExampleDecoder
from official.vision.ops.preprocess_ops import resize_and_crop_image


# ---------------------------------------------------------------------------
# Event log I/O
# ---------------------------------------------------------------------------

def get_event_log_paths(dir: str) -> dict:
    """Return a dict mapping filename → directory for all XES files under *dir*."""
    list_of_files = {}
    for dir_path, _, filenames in os.walk(dir):
        for filename in filenames:
            if filename.endswith('.xes'):
                list_of_files[filename] = dir_path

    assert len(list_of_files) > 0, f"{dir} is empty"
    return list_of_files


def import_event_log(path: str, name: str) -> EventLog:
    """Read an XES event log and return an EventLog object."""
    variant = xes_importer.Variants.ITERPARSE
    parameters = {variant.value.Parameters.TIMESTAMP_SORT: True,
                  variant.value.Parameters.SHOW_PROGRESS_BAR: False}
    return xes_importer.apply(os.path.join(path, name),
                              variant=variant, parameters=parameters)


def filter_complete_events(log: EventLog) -> EventLog:
    """Filter event log to retain only 'complete' / 'COMPLETE' lifecycle events."""
    try:
        return attributes_filter.apply_events(log, ["complete", "COMPLETE"],
            parameters={
                attributes_filter.Parameters.ATTRIBUTE_KEY: "lifecycle:transition",
                attributes_filter.Parameters.POSITIVE: True})
    except Exception:
        return log


def filter_complete_events_uppercase(log: EventLog) -> EventLog:
    """Filter event log to retain only 'COMPLETE' lifecycle events."""
    try:
        return attributes_filter.apply_events(log, ["COMPLETE"],
            parameters={
                attributes_filter.Parameters.ATTRIBUTE_KEY: "lifecycle:transition",
                attributes_filter.Parameters.POSITIVE: True})
    except Exception:
        return log


def get_number_of_traces(event_log: EventLog) -> int:
    return len(event_log)


def check_dfg_graph_freq(log: pd.DataFrame) -> float:
    graph, sa, ea = discover_dfg_typed(log)
    return sum(graph.values())


# ---------------------------------------------------------------------------
# String / data helpers
# ---------------------------------------------------------------------------

def special_string_2_list(s: str) -> list:
    """Parse a string like '[1,2,3]' into a list of ints."""
    return list(map(int, s.translate({ord(i): None for i in "[]"}).split(",")))


def special_string_2_list_float(s: str) -> list:
    """Parse a string like '[1.0,2.0]' into a list of floats."""
    return list(map(float, s.translate({ord(i): None for i in "[]"}).split(",")))


def datetime_2_str(date: dt.datetime) -> str:
    return dt.datetime.strftime(date, "%m-%d-%Y")


def get_timestamp() -> str:
    """Return current time as a timestamp string (Europe/Berlin)."""
    europe = pytz.timezone("Europe/Berlin")
    return dt.datetime.now(europe).strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def matrix_to_img(matrix: np.ndarray, number: int, exp_path: str, mode="color"):
    """Convert a similarity matrix to a JPEG image and save it."""
    if mode == "color":
        cm = plt.get_cmap('viridis')
        colored_image = cm(matrix)
        im = Image.fromarray((colored_image[:, :, :3] * 255).astype(np.uint8))
    elif mode == "gray":
        im = Image.fromarray(matrix).convert("RGB")

    im.save(os.path.join(exp_path, f"{number}.jpg"))


def load_image(path: str) -> np.ndarray:
    return np.array(Image.open(path))


def build_inputs_for_object_detection(image, input_image_size):
    """Resize and pad image to *input_image_size* for model inference.

    Source: https://www.tensorflow.org/tfmodels/vision/object_detection
    """
    image, _ = resize_and_crop_image(
        image,
        input_image_size,
        padded_size=input_image_size,
        aug_scale_min=1.0,
        aug_scale_max=1.0)
    return image


# ---------------------------------------------------------------------------
# Model / decoder helpers
# ---------------------------------------------------------------------------

def get_ex_decoder() -> Tuple[dict, TfExampleDecoder]:
    """Return the category index (drift-type mapping) and a TfExampleDecoder."""
    category_index = {
        1: {"id": 1, "name": "sudden",      "color": "white"},
        2: {"id": 2, "name": "gradual",     "color": "dodgerblue"},
        3: {"id": 3, "name": "incremental", "color": "magenta"},
        4: {"id": 4, "name": "recurring",   "color": "aqua"},
    }
    return category_index, TfExampleDecoder()


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

def visualize_boxes_and_labels(image: np.ndarray, bboxes: list, labels: list,
                               score: list, category_index: dict,
                               is_groundtruth: bool, encoding="winsim"):
    """Draw bounding boxes and drift-type labels onto *image* (in-place).

    TensorFlow bbox format is [y_min, x_min, y_max, x_max].
    """
    ax = plt.gca()

    for i, (box, cls) in enumerate(zip(bboxes, labels)):
        ty1, tx1, y2, x2 = box

        if is_groundtruth:
            text = "{}: GT".format(category_index[cls]["name"])
            edgecolor = "black"
            im_height, im_width, _ = image.shape
            tx1, ty1, x2, y2 = (tx1 * im_width, ty1 * im_height,
                                 x2 * im_width, y2 * im_height)
            text_pos = (y2 + (image.shape[0] * 0.03)
                        if encoding == "winsim"
                        else y2 - (image.shape[0] * 0.03))
        else:
            text = "{}: {}%".format(
                category_index[cls]["name"], int(np.round(score[i] * 100, 0)))
            text_pos = (ty1 - (image.shape[0] * 0.02)
                        if encoding == "winsim"
                        else ty1 + (image.shape[0] * 0.03))
            edgecolor = category_index[cls]["color"]

        tw, th = x2 - tx1, y2 - ty1
        ax.add_patch(plt.Rectangle([tx1, ty1], tw, th,
                                   fill=False, edgecolor=edgecolor, linewidth=1))
        ax.text(tx1, text_pos, text,
                bbox={"facecolor": edgecolor, "alpha": 0.75, "linewidth": 0},
                clip_box=ax.clipbox, clip_on=True, fontsize=8,
                color="white", weight='bold')
