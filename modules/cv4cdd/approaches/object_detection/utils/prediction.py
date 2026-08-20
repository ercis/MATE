import os
import json
import tensorflow as tf
import numpy as np
import pandas as pd
import datetime as dt
import matplotlib.pyplot as plt

import utils.preprocessing as pp
import utils.utilities as utils
import utils.vdd_helper as vdd_helper
import utils.vdd_data_analysis as vdd
import utils.config as cfg

from tqdm import tqdm
from typing import List, Union


# ---------------------------------------------------------------------------
# Data-loading helpers (formerly in utils/evaluation.py)
# ---------------------------------------------------------------------------

def open_file(path: str):
    return open(path)


def get_image_paths(dir: str) -> dict:
    """Get image names and paths in directory."""
    list_of_files = {}
    for dir_path, _, filenames in os.walk(dir):
        for filename in filenames:
            if filename.endswith('.jpg'):
                list_of_files[filename] = dir_path
    return list_of_files


def get_log_matching(data_dir: str) -> pd.DataFrame:
    """Load log matching from file."""
    log_matching_path = os.path.join(data_dir, "log_matching.csv")
    assert os.path.isfile(log_matching_path), "No log matching file found"
    log_matching = pd.read_csv(log_matching_path, index_col=0)
    log_matching = log_matching.rename_axis("log_name").reset_index()
    return log_matching


def get_window_info(data_dir: str) -> Union[list, dict]:
    """Load window info from file."""
    window_info_path = os.path.join(data_dir, "window_info.json")
    assert os.path.isfile(window_info_path), "No window info file found"
    return json.load(open_file(window_info_path))


def get_date_info(data_dir: str) -> Union[list, dict]:
    """Load date info from file."""
    date_info_path = os.path.join(data_dir, "date_info.json")
    assert os.path.isfile(date_info_path), "No date info file found"
    return json.load(open_file(date_info_path))


def get_first_timestamps_vdd(data_dir: str) -> Union[list, dict]:
    """Load first timestamps for VDD from file."""
    first_timestamps_path = os.path.join(data_dir, "first_timestamps.json")
    assert os.path.isfile(first_timestamps_path), "No timestamps file found"
    return json.load(open_file(first_timestamps_path))


def get_predicted_classes(y_pred: list, category_index: dict) -> list:
    """Map numeric class IDs to drift-type name strings."""
    return [category_index.get(y, {}).get("name") for y in y_pred]


def str_2_date(date: str) -> dt.date:
    """Convert string of format '%m-%d-%Y' to a date object."""
    return dt.datetime.strptime(date, "%m-%d-%Y").date()


def nearest(items: list, pivot):
    """Return the nearest item from *items* that is <= pivot."""
    return min([i for i in items if i <= pivot], key=lambda x: abs(x - pivot))


def get_sudden_changepoint_winsim(xmin: int) -> int:
    """Compute changepoint for sudden drifts in WINSIM encoding."""
    if cfg.RESIZE_SUDDEN_BBOX:
        if xmin == 0:
            return int(cfg.RESIZE_VALUE / 2)
        elif (xmin + cfg.RESIZE_VALUE) == cfg.N_WINDOWS:
            return int(cfg.N_WINDOWS - (cfg.RESIZE_VALUE / 2))
        else:
            return xmin + cfg.RESIZE_VALUE
    else:
        return cfg.N_WINDOWS if xmin == (cfg.N_WINDOWS - 1) else xmin


def get_sudden_changepoint_vdd(xmin: int) -> int:
    """Compute changepoint for sudden drifts in VDD encoding."""
    factor = 0.02 * cfg.TARGETSIZE
    if cfg.RESIZE_SUDDEN_BBOX:
        if xmin == 0:
            return int(factor / 2)
        elif int(xmin + factor) >= cfg.TARGETSIZE:
            return int(cfg.TARGETSIZE - (factor / 2))
        else:
            return int(xmin + factor)
    else:
        return cfg.TARGETSIZE if xmin == (cfg.TARGETSIZE - 10) else xmin


# ---------------------------------------------------------------------------
# Preprocessing pipelines
# ---------------------------------------------------------------------------

def prediction_pipeline(log_dir: str, encoding_type: str, output_path: str,
                        cp_all=None, n_windows=None) -> str:
    """Preprocess event logs into images and return the image directory.

    Args:
        log_dir: Event log directory
        encoding_type: 'winsim' or 'vdd'
        output_path: Output directory
        cp_all: VDD measure (optional)
        n_windows: Number of windows for WINSIM (optional)

    Returns:
        Path to the generated image directory
    """
    image_dir = os.path.join(output_path, f"{encoding_type}_images")

    if not os.path.isdir(image_dir):
        os.makedirs(image_dir)

    if encoding_type == "vdd":
        vdd_pipeline(log_dir, output_path=image_dir, cp_all=cp_all)
    elif encoding_type == "winsim":
        winsim_pipeline(log_dir, output_path=image_dir, n_windows=n_windows)
    else:
        raise ValueError("Please specify a valid encoding type: 'vdd' or 'winsim'")
    return image_dir


def vdd_pipeline(log_dir: str, output_path: str, cp_all: bool):
    """VDD pipeline: encode event logs as images for prediction.

    Requires MINERful distribution path set in config (MINERFUL_SCRIPTS_DIR)
    and WINDOW_SYSTEM flag. Adapt SUB_L and SLI_BY in config as needed.
    """
    log_files = utils.get_event_log_paths(log_dir)

    date_info = {}
    first_timestamps = {}

    for name, path in tqdm(log_files.items(), desc="Preprocessing Event Logs",
                           unit="Event Log"):

        log_path = os.path.join(path, name)
        real_name = name.split(".")[0]

        event_log = utils.import_event_log(path=path, name=name)
        filtered_log = utils.filter_complete_events(event_log)

        minerful_csv_path = vdd_helper.vdd_mine_minerful_for_declare_constraints(
            name, log_path, output_path)

        ts_ticks = vdd_helper.vdd_save_separately_timestamp_for_each_constraint_window(
            filtered_log)

        first_timestamps[real_name] = vdd_helper.get_first_timestamp_per_trace(
            filtered_log)

        constraints = vdd_helper.vdd_import_minerful_constraints_timeseries_data(
            minerful_csv_path)

        try:
            constraints, _, _, _, _, _ = vdd.do_cluster_changePoint(
                constraints, cp_all=cp_all)
        except ValueError:
            # Edge case: changepoints cannot be determined; skip this log
            continue

        log_date_info = vdd_helper.vdd_draw_drift_map_prediction(
            data=constraints,
            number=real_name,
            exp_path=output_path,
            ts_ticks=ts_ticks)

        date_info[real_name] = log_date_info

    with open(os.path.join(output_path, "date_info.json"), "w", encoding='utf-8') as f:
        json.dump(date_info, f)

    with open(os.path.join(output_path, "first_timestamps.json"), "w", encoding='utf-8') as f:
        json.dump(first_timestamps, f)


def winsim_pipeline(log_dir: str, output_path: str, n_windows: int):
    """WINSIM pipeline: encode event logs as images for prediction."""
    log_files = utils.get_event_log_paths(log_dir)

    window_info = {}

    for name, path in tqdm(log_files.items(), desc="Preprocessing Event Logs",
                           unit="Event Log"):
        real_name = name.split(".")[0]

        event_log = utils.import_event_log(path=path, name=name)
        filtered_log = utils.filter_complete_events(event_log)

        windowed_dfg_matrices, _, window_information, _ = \
            pp.log_to_windowed_dfg_count(filtered_log, n_windows)

        window_info[real_name] = window_information

        sim_matrix = pp.similarity_calculation(windowed_dfg_matrices)

        utils.matrix_to_img(matrix=sim_matrix,
                            number=real_name,
                            exp_path=output_path,
                            mode="color")

    with open(os.path.join(output_path, "window_info.json"), "w", encoding='utf-8') as f:
        json.dump(window_info, f)


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------

def save_pred_results(results: dict, output_path: str):
    """Save prediction results to a CSV file."""
    results_df = pd.DataFrame.from_dict(results, orient="index")
    save_path = os.path.join(output_path, "prediction_results.csv")
    results_df.to_csv(save_path, sep=",")


# ---------------------------------------------------------------------------
# Changepoint extraction
# ---------------------------------------------------------------------------

def get_changepoints_trace_idx_winsim(bboxes: list, y_pred: list,
                                      window_info: dict) -> List[tuple]:
    """Convert predicted bounding boxes to trace-index changepoints (WINSIM)."""
    change_points = []
    if len(bboxes) == 0:
        return change_points

    for i, bbox in enumerate(bboxes):
        if y_pred[i] == "sudden":
            change_point = get_sudden_changepoint_winsim(round(bbox[0]))
            change_point_trace_id = (window_info[str(change_point)][0],
                                     window_info[str(change_point)][0])
        else:
            change_start = max(1, round(bbox[0]))
            change_end = min(200, round(bbox[2]))
            change_point_trace_id = (window_info[str(change_start)][0],
                                     window_info[str(change_end)][0])
        change_points.append(change_point_trace_id)
    return change_points


def get_changepoints_trace_idx_vdd(bboxes: list, y_pred: list,
                                   timestamps_per_trace: dict,
                                   min_date: dt.date, max_date: dt.date,
                                   targetsize: int) -> List[tuple]:
    """Convert predicted bounding boxes to trace-index changepoints (VDD).

    The x-axis represents the time period, so the changepoint is derived from
    the relative horizontal position of the bounding box.
    """
    change_points = []
    if len(bboxes) == 0:
        return change_points

    day_delta = max_date - min_date
    for i, bbox in enumerate(bboxes):
        if y_pred[i] == "sudden":
            xmin = get_sudden_changepoint_vdd(int(bbox[0]))
            relative_xmin = xmin / targetsize
            change_point_date = min_date + dt.timedelta(
                days=int(day_delta.days * relative_xmin))
            closest_trace = get_closest_trace_index(change_point_date,
                                                    timestamps_per_trace)
            change_point_index = (closest_trace, closest_trace)
        else:
            xmin, xmax = bbox[0], bbox[2]
            relative_xmin = xmin / targetsize
            relative_xmax = xmax / targetsize
            change_start_date = min_date + dt.timedelta(
                days=int(day_delta.days * relative_xmin))
            change_end_date = min_date + dt.timedelta(
                days=int(day_delta.days * relative_xmax))
            change_start_index = get_closest_trace_index(change_start_date,
                                                         timestamps_per_trace)
            change_end_index = get_closest_trace_index(change_end_date,
                                                       timestamps_per_trace)
            change_point_index = (change_start_index, change_end_index)
        change_points.append(change_point_index)
    return change_points


def get_closest_trace_index(drift_moment_date: dt.date,
                            timetamps_per_trace: dict) -> int:
    """Return the trace ID whose first timestamp is closest to *drift_moment_date*."""
    timestamps_df = pd.DataFrame.from_dict(timetamps_per_trace,
                                           orient="index",
                                           columns=["timestamp"])
    timestamps_df = timestamps_df.rename_axis("trace_id").reset_index()
    timestamps_df["timestamp"] = timestamps_df["timestamp"].apply(
        lambda _: dt.datetime.strptime(_, "%m-%d-%Y").date())

    index = timestamps_df.loc[
        timestamps_df["timestamp"] == nearest(timestamps_df["timestamp"].to_list(),
                                              drift_moment_date)
    ].index[0]

    return timestamps_df.iloc[index]["trace_id"]


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

def visualize_prediction(path: str, image: np.ndarray, image_name: str,
                         bbox_pred: np.ndarray, y_pred: np.ndarray,
                         score: np.ndarray, encoding: str):
    """Overlay predicted bounding boxes on the input image and save to disk."""
    category_index, _ = utils.get_ex_decoder()

    plt.figure(figsize=(10, 10))

    image = image[0].numpy()

    utils.visualize_boxes_and_labels(image=image,
                                     bboxes=bbox_pred,
                                     labels=y_pred,
                                     score=score,
                                     category_index=category_index,
                                     is_groundtruth=False,
                                     encoding=encoding)
    plt.imshow(image)
    plt.axis('off')

    plt.savefig(os.path.join(path, f"{image_name}.png"), bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------------
# Main prediction entry point
# ---------------------------------------------------------------------------

def predict(image_dir: str, output_path: str, model: tf.keras.Model,
            encoding_type: str, n_windows=None):
    """Run drift detection on all images in *image_dir* and write results to CSV.

    Args:
        image_dir: Directory containing preprocessed images
        output_path: Directory where results and visualizations are saved
        model: Loaded TensorFlow saved model
        encoding_type: 'winsim' or 'vdd'
        n_windows: Number of WINSIM windows (required when encoding_type='winsim')
    """
    input_image_size = (256, 256)
    targetsize = 256
    threshold = 0.5
    model_fn = model.signatures['serving_default']
    pred_results = {}

    if os.path.isfile(os.path.join(image_dir, "log_matching.csv")):
        log_matching = get_log_matching(image_dir)
    else:
        log_matching = None

    if encoding_type == "winsim":
        window_info = get_window_info(image_dir)
    elif encoding_type == "vdd":
        timestamps_per_trace = get_first_timestamps_vdd(image_dir)
        date_info = get_date_info(image_dir)
    else:
        raise ValueError("Please specify a valid encoding type: 'vdd' or 'winsim'")

    category_index, _ = utils.get_ex_decoder()

    images = get_image_paths(image_dir)

    for image_name, image_path in tqdm(images.items(),
                                       desc="Detecting Concept Drift", unit="images"):

        path = os.path.join(image_path, image_name)
        image_name = image_name.split(".")[0]
        image = utils.load_image(path)
        image = utils.build_inputs_for_object_detection(image, input_image_size)
        image = tf.expand_dims(image, axis=0)
        image = tf.cast(image, dtype=tf.uint8)
        result = model_fn(image)

        scores = result['detection_scores'][0].numpy()
        confidence_scores = scores[scores > threshold]

        bbox_pred = result['detection_boxes'][0].numpy()
        bbox_pred = bbox_pred[scores > threshold]

        y_pred = result['detection_classes'][0].numpy().astype(int)
        y_pred = y_pred[scores > threshold]

        y_pred_category = get_predicted_classes(y_pred, category_index)

        visualize_prediction(path=output_path,
                             image=image,
                             image_name=image_name,
                             bbox_pred=bbox_pred,
                             y_pred=y_pred,
                             score=confidence_scores,
                             encoding=encoding_type)

        if encoding_type == "winsim":
            bbox_pred = bbox_pred / targetsize * n_windows
            if log_matching is not None:
                log_name = log_matching.loc[log_matching["image_id"] ==
                                            int(image_name), "log_name"].iloc[0]
                log_window_info = window_info[log_name]
            else:
                log_window_info = window_info[image_name]
            pred_change_points = get_changepoints_trace_idx_winsim(
                bbox_pred, y_pred_category, log_window_info)
        elif encoding_type == "vdd":
            if log_matching is not None:
                log_name = log_matching.loc[log_matching["image_id"] ==
                                            int(image_name), "log_name"].iloc[0]
                min_date, max_date = date_info[log_name]
                pred_change_points = get_changepoints_trace_idx_vdd(
                    bboxes=bbox_pred,
                    y_pred=y_pred_category,
                    timestamps_per_trace=timestamps_per_trace[log_name],
                    min_date=str_2_date(min_date),
                    max_date=str_2_date(max_date),
                    targetsize=targetsize)
            else:
                min_date, max_date = date_info[image_name]
                pred_change_points = get_changepoints_trace_idx_vdd(
                    bboxes=bbox_pred,
                    y_pred=y_pred_category,
                    timestamps_per_trace=timestamps_per_trace[image_name],
                    min_date=str_2_date(min_date),
                    max_date=str_2_date(max_date),
                    targetsize=targetsize)

        pred_results[image_name] = {
            "Detected Changepoints": pred_change_points,
            "Detected Drift Types": y_pred_category,
            "Prediction Confidence": np.round(confidence_scores, decimals=4)
        }

    save_pred_results(pred_results, output_path)
