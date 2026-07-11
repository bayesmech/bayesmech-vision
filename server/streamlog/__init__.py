"""Compatibility package for Python analyzer helpers.

The Streamlog runtime is implemented in Rust. Python analyzer scripts still
reuse ``streamlog.protoio.ProtoIO`` for length-delimited protobuf artifacts.
"""
