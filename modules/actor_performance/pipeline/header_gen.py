"""Generate the promg semantic header + dataset description for the generic log.

The reference implementation hardcodes a BPIC17 header with Application/Workflow/Offer
entities plus a combined ``CaseAWO`` entity. The decomposition itself only ever uses the
combined case entity (``sysId = case column``) and the resource entity — so the generic
header below, with just ``Case`` + ``Resource``, reproduces the paper's numbers exactly
(validated against the reference pipeline on the BPIC17 sample: 203/203 aggregate rows
identical).

Entity type names drive promg's relationship labels:
``DF_{TYPE.upper()}`` for event-level directly-follows and ``DF_TI_{Type}`` for the
task-instance level (promg's own task_identification_ql convention).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CASE_ENTITY = "Case"
RESOURCE_ENTITY = "Resource"

DF_CASE = f"DF_{CASE_ENTITY.upper()}"
DF_RESOURCE = f"DF_{RESOURCE_ENTITY.upper()}"
DF_TI_CASE = f"DF_TI_{CASE_ENTITY}"
DF_TI_RESOURCE = f"DF_TI_{RESOURCE_ENTITY}"

# apoc.date.convertFormat parse pattern for prep.py's "%Y/%m/%d %H:%M:%S.%f"[:-3]
# strings; the offset is appended by promg before parsing (prep normalizes to UTC).
# Same pattern the validated BPIC17 reference run used.
TIMESTAMP_FORMAT = "y/M/d H:m:s.nX"
TIMEZONE_OFFSET = "+00"


def semantic_header(dataset_name: str) -> dict[str, Any]:
    return {
        "name": dataset_name,
        "version": "1.0.0",
        "records": ["(record:EventRecord {timestamp, activity, lifecycle, case, resource})"],
        "nodes": [
            {
                "type": "Event",
                "constructor": [
                    {
                        "prevalent_record": "(record:EventRecord)",
                        "result": (
                            "(e:Event {timestamp:record.timestamp, "
                            "activity:record.activity, lifecycle:record.lifecycle})"
                        ),
                    }
                ],
            },
            {
                "type": CASE_ENTITY,
                "constructor": [
                    {
                        "prevalent_record": "(record:EventRecord)",
                        "result": f"(c:Entity:{CASE_ENTITY} {{sysId: record.case}})",
                        "infer_corr_from_event_record": True,
                    }
                ],
                "infer_df": True,
                "include_label_in_df": True,
                "merge_duplicate_df": True,
            },
            {
                "type": RESOURCE_ENTITY,
                "constructor": [
                    {
                        "prevalent_record": "(record:EventRecord)",
                        "result": f"(r:Entity:{RESOURCE_ENTITY} {{sysId: record.resource}})",
                        "infer_corr_from_event_record": True,
                    }
                ],
                "infer_df": True,
                "include_label_in_df": True,
                "merge_duplicate_df": True,
            },
        ],
        "relations": [],
    }


def dataset_description(
    dataset_name: str, file_directory: Path, file_name: str
) -> list[dict[str, Any]]:
    return [
        {
            "name": dataset_name,
            # promg splits on backslash and joins onto cwd; an absolute POSIX
            # path passes through os.path.join unchanged.
            "file_directory": str(file_directory),
            "file_name": file_name,
            "labels": ["EventRecord"],
            "add_log": True,
            "add_index": True,
            "attributes": [
                {"name": "activity", "columns": [{"name": "activity"}], "optional": False},
                {"name": "lifecycle", "columns": [{"name": "lifecycle"}], "optional": False},
                {
                    "name": "timestamp",
                    "columns": [{"name": "timestamp"}],
                    "datetime_object": {
                        "format": TIMESTAMP_FORMAT,
                        "timezone_offset": TIMEZONE_OFFSET,
                    },
                    "optional": False,
                },
                {"name": "case", "columns": [{"name": "case"}], "optional": False},
                {"name": "resource", "columns": [{"name": "resource"}], "optional": False},
            ],
        }
    ]


def write_config_files(
    target_dir: Path, dataset_name: str, csv_dir: Path, csv_name: str
) -> tuple[Path, Path]:
    """Write both JSONs into ``target_dir``; returns (header_path, ds_path)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    header_path = target_dir / "semantic_header.json"
    ds_path = target_dir / "dataset_description.json"
    header_path.write_text(json.dumps(semantic_header(dataset_name), indent=2))
    ds_path.write_text(json.dumps(dataset_description(dataset_name, csv_dir, csv_name), indent=2))
    return header_path, ds_path
