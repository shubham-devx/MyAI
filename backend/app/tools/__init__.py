"""Modular, explicitly registered assistant tools.

Every tool receives validated arguments from a single registry entry point. Tools return
plain data; they never render HTML and never execute code on the host without the
container sandbox.
"""
