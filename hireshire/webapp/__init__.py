"""HireShire web dashboard (Phase 5).

A local FastAPI app that reads the shared SQLite datastore (read-only), exposes
the phase configs for view/edit, controls pipeline runs as subprocesses, and
serves a LangGraph "talk to your data" chat agent. Built additively — the
pipeline phases are imported, never modified (bar the enable_tuner/enable_applier
config keys wired into orchestrate.py).
"""
