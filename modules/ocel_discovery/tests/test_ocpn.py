"""OC-PN serializer test - builds a small OCEL, discovers the object-centric
Petri net with pm4py, and asserts the flattened per-object-type shape the panel
consumes."""

from __future__ import annotations

import pandas as pd
import pm4py
from modules.ocel_discovery.module import _serialize_ocpn
from pm4py.objects.ocel.obj import OCEL


def _sample_ocel() -> OCEL:
    eids = ["e1", "e2", "e3"]
    acts = ["create order", "pay", "ship"]
    ts = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"], utc=True)
    events = pd.DataFrame({"ocel:eid": eids, "ocel:activity": acts, "ocel:timestamp": ts})
    relations = pd.DataFrame(
        {
            "ocel:eid": eids,
            "ocel:activity": acts,
            "ocel:timestamp": ts,
            "ocel:oid": ["o1", "o1", "o1"],
            "ocel:type": ["order", "order", "order"],
            "ocel:qualifier": ["", "", ""],
        }
    )
    objects = pd.DataFrame({"ocel:oid": ["o1"], "ocel:type": ["order"]})
    return OCEL(events=events, objects=objects, relations=relations)


def test_serialize_ocpn_shape() -> None:
    ocpn = pm4py.discover_oc_petri_net(_sample_ocel())
    out = _serialize_ocpn(ocpn)

    assert out["object_types"] == ["order"]
    assert {"create order", "pay", "ship"} <= set(out["activities"])
    assert len(out["nets"]) == 1

    net = out["nets"][0]
    assert net["object_type"] == "order"
    assert len(net["places"]) >= 1
    assert len(net["transitions"]) >= 1

    # Every id is prefixed with the object type (names are only locally unique).
    for coll in ("places", "transitions"):
        assert all(item["id"].startswith("order::") for item in net[coll])

    # The single linear order trace yields an initial and a final place.
    assert any(p["is_initial"] for p in net["places"])
    assert any(p["is_final"] for p in net["places"])

    # Labelled transitions cover the activities; the silent flag is a bool.
    labels = {t["label"] for t in net["transitions"] if not t["silent"]}
    assert {"create order", "pay", "ship"} <= labels
    assert all(isinstance(t["silent"], bool) for t in net["transitions"])

    # Arcs reference ids present in this net; the variable flag is a bool.
    node_ids = {p["id"] for p in net["places"]} | {t["id"] for t in net["transitions"]}
    for arc in net["arcs"]:
        assert arc["source"] in node_ids
        assert arc["target"] in node_ids
        assert isinstance(arc["variable"], bool)
