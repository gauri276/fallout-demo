"""Streamlit entry point for the local FALLOUT investigation workbench."""

from fallout.runtime import local_runtime

local_runtime()

from fallout.workbench import render


if __name__ == "__main__":
    render()