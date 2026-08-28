"""Actor-performance pipeline: event log → prepared CSV → Event Knowledge Graph in the
Neo4j sidecar → behavior-decomposed waiting times → cached result dict.

Layout mirrors the stages:

- ``connection``  - resolve settings (config → env → local defaults), bolt ping
- ``prep``        - event-log DataFrame → the 5-column CSV promg imports
- ``header_gen``  - generate the promg semantic header + dataset description
- ``queries``     - rewritten Cypher (behavior classification, extraction, admin)
- ``run``         - orchestration: the blocking pipeline the @job drives

Everything except ``run`` is importable without promg / a live server, so unit
tests run in the platform venv.
"""
