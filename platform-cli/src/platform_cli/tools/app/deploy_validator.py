"""Validate that app builds exist for a given git hash before deploy."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover

    class PlatformError(RuntimeError):
        def __init__(
            self,
            message: str,
            code: str = "E_INTERNAL",
            reason: str = "UNKNOWN",
            alert: bool = False,
        ):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert


def validate_builds_for_hash(
    apps: List[Any],
    git_hash: str,
    repo_root: Path,
) -> Dict[str, Dict[str, Any]]:
    """
    Validate that built artifacts exist for each app matching the given git hash.

    For Python apps: checks for .whl files containing the git hash in dist/
    For Scala apps: checks for .jar files in target/

    Args:
        apps: List of AppConfig instances from apps.json
        git_hash: Short git hash to match (e.g., "18e4caa")
        repo_root: Root path of data-product repo

    Returns:
        Dict mapping app path to artifact info:
        {
            "careeverywhere-unified-load": {
                "source_dir": Path(...),
                "wheel": Path(...) or None,
                "jar": Path(...) or None,
                "source_files": [Path(...), ...],
            }
        }

    Raises:
        PlatformError if any app is missing builds for the hash
    """
    results: Dict[str, Dict[str, Any]] = {}
    missing_builds: List[str] = []

    for app in apps:
        app_dir = repo_root / "apps" / app.path
        if not app_dir.exists():
            missing_builds.append(f"{app.path} (directory not found)")
            continue

        app_info: Dict[str, Any] = {
            "source_dir": app_dir,
            "wheel": None,
            "jar": None,
            "source_files": [],
        }

        # Collect source files (.py for Python, .scala/.java for Scala)
        for ext in ("*.py", "*.scala", "*.java"):
            app_info["source_files"].extend(app_dir.glob(ext))

        if app.type == "python":
            dist_dir = app_dir / "dist"
            if not dist_dir.exists():
                missing_builds.append(
                    f"{app.path} (no dist/ directory — run 'ghdp build --app {app.path}' first)"
                )
                continue

            # Find wheel matching the git hash
            matching_wheels = [
                w for w in dist_dir.glob("*.whl")
                if git_hash in w.name
            ]

            if not matching_wheels:
                # Check if any wheel exists at all
                all_wheels = list(dist_dir.glob("*.whl"))
                if all_wheels:
                    wheel_names = [w.name for w in all_wheels]
                    missing_builds.append(
                        f"{app.path} (wheel exists but not for hash '{git_hash}': {wheel_names}. "
                        f"Run 'ghdp build --app {app.path}' with current commit.)"
                    )
                else:
                    missing_builds.append(
                        f"{app.path} (no wheel in dist/ — run 'ghdp build --app {app.path}' first)"
                    )
                continue

            app_info["wheel"] = matching_wheels[0]

        elif app.type == "scala":
            target_dir = app_dir / "target"
            if not target_dir.exists():
                missing_builds.append(
                    f"{app.path} (no target/ directory — run 'ghdp build --app {app.path}' first)"
                )
                continue

            jars = [j for j in target_dir.glob("*.jar") if not j.name.startswith("original-")]
            if not jars:
                missing_builds.append(
                    f"{app.path} (no JAR in target/ — run 'ghdp build --app {app.path}' first)"
                )
                continue

            # Maven JARs don't embed git hash in filename. Check the JAR version
            # string contains the expected hash (snapshot versions include it).
            jar = jars[0]
            if git_hash not in jar.name:
                # JAR doesn't contain hash — check pom.xml version was updated
                pom_path = app_dir / "pom.xml"
                if pom_path.exists():
                    pom_text = pom_path.read_text()
                    if git_hash not in pom_text:
                        print(f"  WARNING: Scala JAR {jar.name} may not match commit {git_hash}. "
                              f"Re-run 'ghdp build --app {app.path}' if in doubt.")

            app_info["jar"] = jar

        results[app.path] = app_info

    if missing_builds:
        details = "\n".join(f"  - {m}" for m in missing_builds)
        raise PlatformError(
            f"Build artifacts not found for git hash '{git_hash}':\n{details}\n\n"
            f"Run 'ghdp build' to build all apps, then retry 'ghdp deploy'.",
            code="E_BUILDS_NOT_FOUND",
            reason=f"missing_builds_for_{git_hash}",
        )

    return results
