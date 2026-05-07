from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class clean_build_py(_build_py):
    """Ensure stale files under build/lib do not leak into new wheel installs."""

    def run(self) -> None:
        build_lib = getattr(self, "build_lib", None)
        if build_lib:
            build_path = Path(build_lib)
            if build_path.exists():
                shutil.rmtree(build_path)
        super().run()


setup(cmdclass={"build_py": clean_build_py})
