# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.composition import create_runtime_services
from mind_app.cli.entry import run


if __name__ == "__main__":
    raise SystemExit(run(
        entry_file=__file__,
        runtime_services=create_runtime_services(),
    ))
