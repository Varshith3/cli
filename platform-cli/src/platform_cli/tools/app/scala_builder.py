"""Scala/Maven application building."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict

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


from platform_cli.exec.runner import run_cmd


def _is_ci_friendly_pom(pom_path: Path) -> bool:
    """Check if pom.xml uses CI-friendly ${revision}${changelist} versioning."""
    content = pom_path.read_text()
    return "${revision}" in content


def _update_version_in_pom(pom_path: Path, new_version: str) -> None:
    """
    Update the artifact's own version in pom.xml (legacy fallback).

    Used only for old-style pom.xml files that have a hardcoded version.
    Finds the direct <version> child of <project> and replaces it.
    """
    content = pom_path.read_text()

    try:
        tree = ET.parse(str(pom_path))
        root = tree.getroot()
    except ET.ParseError:
        raise PlatformError(
            f"Invalid XML in {pom_path}",
            code="E_POM_PARSE_FAILED",
            reason=str(pom_path),
        )

    ns_match = re.match(r'\{(.+)\}', root.tag)
    ns = ns_match.group(1) if ns_match else ""
    version_tag = f"{{{ns}}}version" if ns else "version"
    parent_tag = f"{{{ns}}}parent" if ns else "parent"

    # Find direct <version> child of <project> (skip <parent>)
    old_version = None
    for child in root:
        if child.tag == version_tag and child.text:
            old_version = child.text.strip()
            break

    if not old_version:
        raise PlatformError(
            f"Could not find artifact version to update in {pom_path}",
            code="E_VERSION_NOT_FOUND",
            reason=str(pom_path),
        )

    if old_version == new_version:
        return

    # Replace only the <version> after </parent> to avoid modifying parent version
    parent_end = 0
    parent_close_match = re.search(r'</parent\s*>', content)
    if parent_close_match:
        parent_end = parent_close_match.end()

    before_parent = content[:parent_end]
    after_parent = content[parent_end:]
    updated_after = after_parent.replace(
        f"<version>{old_version}</version>",
        f"<version>{new_version}</version>",
        1,
    )
    if updated_after == after_parent:
        raise PlatformError(
            f"Could not locate <version>{old_version}</version> after <parent> block in {pom_path}",
            code="E_VERSION_NOT_FOUND",
            reason=str(pom_path),
        )

    pom_path.write_text(before_parent + updated_after)


def build_scala_app(
    app: Any,  # AppConfig
    context: Dict[str, Any],
    repo_root: Path,
) -> str:
    """
    Build Scala app using Maven (mvn clean package).

    Supports two pom.xml formats:
    - CI-friendly (new): <version>${revision}${changelist}</version>
      → Version injected via -Drevision/-Dchangelist CLI flags. pom.xml never touched.
    - Hardcoded (old): <version>0.1.0-SNAPSHOT</version>
      → Version written into pom.xml, restored after build.

    Args:
        app: AppConfig instance (type=scala)
        context: Build context
        repo_root: Root path of data-product repo

    Returns:
        Path to built artifact (target/ directory)
    """
    app_dir = repo_root / "apps" / app.path

    pom_path = app_dir / "pom.xml"
    if not pom_path.exists():
        raise PlatformError(
            f"pom.xml not found in {app_dir}",
            code="E_POM_NOT_FOUND",
            reason=str(app_dir),
        )

    from platform_cli.tools.app.version_manager import resolve_version
    maven_version, mode = resolve_version(repo_root, "scala")
    print(f"  Version: {maven_version} (mode: {mode})")

    ci_friendly = _is_ci_friendly_pom(pom_path)

    # Build extra Maven args for CI-friendly pom
    version_args = []
    original_pom_content = None
    if ci_friendly:
        # Split "2.35.0-fa1d75a-SNAPSHOT" → revision="2.35.0-fa1d75a", changelist="-SNAPSHOT"
        # Split "2.35.0" → revision="2.35.0", changelist=""
        if maven_version.endswith("-SNAPSHOT"):
            revision = maven_version.removesuffix("-SNAPSHOT")
            changelist = "-SNAPSHOT"
        else:
            revision = maven_version
            changelist = ""
        version_args = [f"-Drevision={revision}", f"-Dchangelist={changelist}"]
    else:
        # Legacy: modify pom.xml directly, restore after build
        original_pom_content = pom_path.read_text()
        _update_version_in_pom(pom_path, maven_version)

    # Clean target/ directory to avoid conflicts with old JARs
    target_dir = app_dir / "target"
    if target_dir.exists():
        print(f"  Cleaning target/ directory...")
        for file in target_dir.glob("*.jar"):
            file.unlink()

    # Check mvn is available
    mvn_check = run_cmd(["mvn", "--version"], check=False, capture=True)
    if mvn_check.returncode != 0:
        raise PlatformError(
            "Maven (mvn) not found. Install Maven to build Scala apps.",
            code="E_MVN_NOT_FOUND",
            reason="mvn_missing",
        )

    # Build with Maven
    print(f"  Building {app.path} with Maven...")
    try:
        build_result = run_cmd(
            ["mvn", "clean", "package", "-DskipTests"] + version_args,
            cwd=str(app_dir),
            check=False,
        )
        if build_result.returncode != 0:
            raise PlatformError(
                f"Maven build failed: {build_result.stderr}",
                code="E_MVN_BUILD_FAILED",
                reason=app.path,
            )
    finally:
        # Restore original pom.xml only if we modified it (legacy mode)
        if original_pom_content is not None:
            pom_path.write_text(original_pom_content)

    return str(target_dir)
