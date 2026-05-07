from __future__ import annotations

import json
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Protocol

from platform_cli.core.errors import PlatformError
from platform_cli.core.github_auth import direct_github_token, gh_subprocess_env, managed_install_token


DEFAULT_PROVIDER = "github_release"
DEFAULT_MANIFEST_ASSET = "content-manifest.json"
MARKETPLACE_PROVIDER = "marketplace_repo"
DEFAULT_MARKETPLACE_BRANCH = "develop"

RunCommand = Callable[..., Any]


@dataclass(frozen=True)
class NormalizedPackageManifest:
    capability: str
    version: str
    target_root_key: str
    target_subdir: str
    files: list[dict[str, str]]

    @property
    def rel_paths(self) -> list[str]:
        return [item["target_path"] for item in self.files]


class SyncProvider(Protocol):
    name: str

    def resolve_version(self, *, source: Mapping[str, Any]) -> str:
        ...

    def download_asset(self, *, source: Mapping[str, Any], asset_name: str, download_dir: Path) -> Path:
        ...

    def load_package_manifest(self, *, source: Mapping[str, Any]) -> NormalizedPackageManifest:
        ...


def normalize_file_bundle_manifest(payload: object) -> NormalizedPackageManifest:
    if not isinstance(payload, dict):
        raise PlatformError(
            "Release content manifest root must be a JSON object.",
            code="E_RELEASE_CONTENT_MANIFEST_INVALID",
            reason="manifest",
        )

    capability = str(payload.get("capability", "")).strip()
    version = str(payload.get("version", "")).strip()
    root_key = str(payload.get("target_root_key", "")).strip()
    target_subdir = str(payload.get("target_subdir", "")).strip()
    files = payload.get("files")

    if not capability or not version or not root_key or not target_subdir or not isinstance(files, list):
        raise PlatformError(
            "Release content manifest is missing required fields.",
            code="E_RELEASE_CONTENT_MANIFEST_INVALID",
            reason="manifest",
        )

    normalized_files: list[dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            raise PlatformError(
                "Release content manifest file entries must be objects.",
                code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                reason="files",
            )
        asset_name = str(item.get("asset_name", "")).strip()
        target_path = str(item.get("target_path", "")).strip()
        if not asset_name or not target_path:
            raise PlatformError(
                "Release content manifest file entries require asset_name and target_path.",
                code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                reason="files",
            )
        normalized_files.append({"asset_name": asset_name, "target_path": target_path})

    if not normalized_files:
        raise PlatformError(
            "Release content manifest contains no installable files.",
            code="E_RELEASE_CONTENT_MANIFEST_INVALID",
            reason="files_empty",
        )

    return NormalizedPackageManifest(
        capability=capability,
        version=version,
        target_root_key=root_key,
        target_subdir=target_subdir,
        files=normalized_files,
    )


def unsupported_provider_error(provider: str) -> PlatformError:
    return PlatformError(
        f"Unsupported sync content provider: {provider}",
        code="E_SYNC_PROVIDER_UNSUPPORTED",
        reason=provider,
    )


@dataclass
class GitHubReleaseProvider:
    run_cmd_impl: RunCommand
    name: str = DEFAULT_PROVIDER

    def resolve_version(self, *, source: Mapping[str, Any]) -> str:
        tag = str(source.get("tag", "")).strip()
        if not tag:
            raise PlatformError(
                "GitHub release source metadata requires tag.",
                code="E_RELEASE_CONTENT_INDEX_INVALID",
                reason="provider_source_fields",
            )
        return tag

    def download_asset(self, *, source: Mapping[str, Any], asset_name: str, download_dir: Path) -> Path:
        repo = str(source.get("repo", "")).strip()
        tag = str(source.get("tag", "")).strip()
        if not repo or not tag:
            raise PlatformError(
                "GitHub release source metadata requires repo and tag.",
                code="E_RELEASE_CONTENT_INDEX_INVALID",
                reason="provider_source_fields",
            )

        token = managed_install_token() or direct_github_token()
        if token:
            try:
                downloaded = self._download_asset_via_api(
                    repo=repo,
                    tag=tag,
                    asset_name=asset_name,
                    download_dir=download_dir,
                    token=token,
                )
                if downloaded.exists():
                    return downloaded
            except PlatformError:
                # Fall through to gh CLI fallback for parity with existing behavior.
                pass

        cmd = [
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            repo,
            "--dir",
            str(download_dir),
            "--clobber",
            "--pattern",
            asset_name,
        ]
        try:
            self.run_cmd_impl(cmd, check=True, capture=True, env=gh_subprocess_env())
        except PlatformError as e:
            raise PlatformError(
                f"Failed to download release asset '{asset_name}' from {repo}@{tag}: {e}",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason=asset_name,
            )

        downloaded = download_dir / asset_name
        if not downloaded.exists():
            raise PlatformError(
                f"Downloaded release asset '{asset_name}' was not found in '{download_dir}'.",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason=asset_name,
            )
        return downloaded

    def _download_asset_via_api(
        self,
        *,
        repo: str,
        tag: str,
        asset_name: str,
        download_dir: Path,
        token: str,
    ) -> Path:
        encoded_tag = urllib.parse.quote(tag, safe="")
        release_url = f"https://api.github.com/repos/{repo}/releases/tags/{encoded_tag}"
        release_req = urllib.request.Request(release_url)
        release_req.add_header("Accept", "application/vnd.github+json")
        release_req.add_header("Authorization", f"Bearer {token}")
        release_req.add_header("X-GitHub-Api-Version", "2022-11-28")

        try:
            with urllib.request.urlopen(release_req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise PlatformError(
                f"Failed to resolve release metadata from GitHub API: {e}",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason=asset_name,
            )

        assets = payload.get("assets", []) if isinstance(payload, dict) else []
        if not isinstance(assets, list):
            assets = []
        asset_id = None
        for item in assets:
            if isinstance(item, dict) and str(item.get("name", "")).strip() == asset_name:
                asset_id = item.get("id")
                break
        if not asset_id:
            raise PlatformError(
                f"Release asset '{asset_name}' was not found in {repo}@{tag}.",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason=asset_name,
            )

        asset_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
        asset_req = urllib.request.Request(asset_url)
        asset_req.add_header("Accept", "application/octet-stream")
        asset_req.add_header("Authorization", f"Bearer {token}")
        asset_req.add_header("X-GitHub-Api-Version", "2022-11-28")
        target_path = download_dir / asset_name

        try:
            with urllib.request.urlopen(asset_req, timeout=60) as resp:
                target_path.write_bytes(resp.read())
        except Exception as e:
            raise PlatformError(
                f"Failed to download release asset via GitHub API: {e}",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason=asset_name,
            )
        return target_path

    def load_package_manifest(self, *, source: Mapping[str, Any]) -> NormalizedPackageManifest:
        manifest_asset = str(source.get("manifest_asset", DEFAULT_MANIFEST_ASSET)).strip() or DEFAULT_MANIFEST_ASSET
        with TemporaryDirectory(prefix="ghdp_release_content_") as tmpdir:
            download_dir = Path(tmpdir)
            asset_path = self.download_asset(source=source, asset_name=manifest_asset, download_dir=download_dir)
            try:
                payload = json.loads(asset_path.read_text(encoding="utf-8-sig"))
            except Exception as e:
                raise PlatformError(
                    f"Failed to parse JSON asset '{manifest_asset}': {e}",
                    code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                    reason=manifest_asset,
                )
        return normalize_file_bundle_manifest(payload)


@dataclass(frozen=True)
class _SnapshotRef:
    checkout_root: Path
    commit: str


_SNAPSHOT_CACHE: dict[str, tuple[TemporaryDirectory[str], _SnapshotRef]] = {}


@dataclass
class MarketplaceRepoProvider:
    run_cmd_impl: RunCommand
    name: str = MARKETPLACE_PROVIDER

    def resolve_version(self, *, source: Mapping[str, Any]) -> str:
        return self._resolve_commit(source)

    def download_asset(self, *, source: Mapping[str, Any], asset_name: str, download_dir: Path) -> Path:
        snapshot = self._ensure_snapshot(source)
        content_path = self._content_path(source)
        source_file = snapshot.checkout_root / content_path / asset_name
        if not source_file.exists() or not source_file.is_file():
            raise PlatformError(
                f"Marketplace content asset '{asset_name}' was not found under '{content_path}'.",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason=asset_name,
            )
        target = download_dir / asset_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_file.read_bytes())
        return target

    def load_package_manifest(self, *, source: Mapping[str, Any]) -> NormalizedPackageManifest:
        snapshot = self._ensure_snapshot(source)
        install_unit_type = self._install_unit_type(source)
        content_path = self._content_path(source)
        content_root = snapshot.checkout_root / content_path
        if not content_root.exists() or not content_root.is_dir():
            raise PlatformError(
                f"Marketplace content path '{content_path}' was not found in the source repo.",
                code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                reason="content_path",
            )

        target_root_key = str(source.get("target_root_key", "")).strip()
        if install_unit_type == "skill":
            skill_file = content_root / "SKILL.md"
            if not skill_file.exists():
                raise PlatformError(
                    f"Marketplace skill path '{content_path}' does not contain SKILL.md.",
                    code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                    reason="skill_path",
                )
        elif install_unit_type == "plugin":
            desired_plugin_dir = self._desired_plugin_manifest_dir(target_root_key)
            available_plugin_manifest = self._resolve_plugin_manifest_path(content_root)
            if available_plugin_manifest is None:
                raise PlatformError(
                    f"Marketplace plugin path '{content_path}' does not contain a supported plugin manifest.",
                    code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                    reason="plugin_path",
                )
        else:
            raise PlatformError(
                f"Unsupported marketplace install unit type '{install_unit_type}'.",
                code="E_RELEASE_CONTENT_INDEX_INVALID",
                reason="provider_source_fields",
            )

        files = self._build_manifest_file_specs(
            content_root=content_root,
            install_unit_type=install_unit_type,
            desired_plugin_dir=desired_plugin_dir if install_unit_type == "plugin" else None,
        )
        if not files:
            raise PlatformError(
                f"Marketplace content path '{content_path}' contains no installable files.",
                code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                reason="files_empty",
            )

        if not target_root_key:
            raise PlatformError(
                "Marketplace skill source metadata requires target_root_key.",
                code="E_RELEASE_CONTENT_INDEX_INVALID",
                reason="provider_source_fields",
            )
        target_subdir = str(source.get("target_subdir", "")).strip() or Path(content_path).name
        capability = str(source.get("capability", "")).strip() or target_subdir

        return NormalizedPackageManifest(
            capability=capability,
            version=snapshot.commit,
            target_root_key=target_root_key,
            target_subdir=target_subdir,
            files=files,
        )

    def list_plugin_skill_paths(self, *, source: Mapping[str, Any], plugin_name: str) -> list[str]:
        snapshot = self._ensure_snapshot(source)
        plugin_root = snapshot.checkout_root / "plugins" / plugin_name / "skills"
        if not plugin_root.exists():
            raise PlatformError(
                f"Marketplace plugin '{plugin_name}' was not found in the source repo.",
                code="E_SYNC_POLICY_INVALID",
                reason=plugin_name,
            )
        discovered = sorted(
            str((path / "SKILL.md").parent.relative_to(snapshot.checkout_root)).replace("\\", "/")
            for path in plugin_root.iterdir()
            if path.is_dir() and (path / "SKILL.md").exists()
        )
        if not discovered:
            raise PlatformError(
                f"Marketplace plugin '{plugin_name}' contains no installable skills.",
                code="E_SYNC_POLICY_INVALID",
                reason=plugin_name,
            )
        return discovered

    def _resolve_commit(self, source: Mapping[str, Any]) -> str:
        commit = str(source.get("commit", "")).strip()
        if commit:
            return commit

        branch = str(source.get("branch", DEFAULT_MARKETPLACE_BRANCH)).strip() or DEFAULT_MARKETPLACE_BRANCH
        repo_path = str(source.get("repo_path", "")).strip()
        if repo_path:
            res = self.run_cmd_impl(["git", "-C", repo_path, "rev-parse", branch], check=True, capture=True)
            commit = res.stdout.strip()
        else:
            repo = str(source.get("repo", "")).strip()
            if not repo:
                raise PlatformError(
                    "Marketplace repo source metadata requires either repo or repo_path.",
                    code="E_RELEASE_CONTENT_INDEX_INVALID",
                    reason="provider_source_fields",
                )
            res = self.run_cmd_impl(
                ["gh", "api", f"repos/{repo}/commits/{branch}"],
                check=True,
                capture=True,
                env=gh_subprocess_env(),
            )
            try:
                commit = str(json.loads(res.stdout).get("sha", "")).strip()
            except Exception as e:
                raise PlatformError(
                    f"Failed to parse commit metadata for marketplace repo '{repo}@{branch}': {e}",
                    code="E_RELEASE_CONTENT_INDEX_INVALID",
                    reason="provider_source_fields",
                )
        if not commit:
            raise PlatformError(
                "Marketplace repo source metadata could not be resolved to a commit SHA.",
                code="E_RELEASE_CONTENT_INDEX_INVALID",
                reason="provider_source_fields",
            )
        return commit

    def _cache_key(self, source: Mapping[str, Any], commit: str) -> str:
        repo = str(source.get("repo", "")).strip()
        repo_path = str(Path(str(source.get("repo_path", "")).strip()).expanduser()) if str(source.get("repo_path", "")).strip() else ""
        return json.dumps({"repo": repo, "repo_path": repo_path, "commit": commit}, sort_keys=True)

    def _ensure_snapshot(self, source: Mapping[str, Any]) -> _SnapshotRef:
        commit = self._resolve_commit(source)
        cache_key = self._cache_key(source, commit)
        cached = _SNAPSHOT_CACHE.get(cache_key)
        if cached is not None:
            return cached[1]

        tempdir = TemporaryDirectory(prefix="ghdp_marketplace_repo_")
        temp_root = Path(tempdir.name)
        archive_path = temp_root / "snapshot.tar"
        extract_root = temp_root / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        repo_path = str(source.get("repo_path", "")).strip()
        if repo_path:
            self.run_cmd_impl(
                ["git", "-C", repo_path, "archive", "--format=tar", "-o", str(archive_path), commit],
                check=True,
                capture=True,
            )
        else:
            repo = str(source.get("repo", "")).strip()
            if not repo:
                raise PlatformError(
                    "Marketplace repo source metadata requires either repo or repo_path.",
                    code="E_RELEASE_CONTENT_INDEX_INVALID",
                    reason="provider_source_fields",
                )
            self._download_remote_snapshot(repo=repo, commit=commit, archive_path=archive_path)

        with tarfile.open(archive_path, "r:*") as tar:
            tar.extractall(path=extract_root)

        checkout_root = self._find_checkout_root(extract_root)
        snapshot = _SnapshotRef(checkout_root=checkout_root, commit=commit)
        _SNAPSHOT_CACHE[cache_key] = (tempdir, snapshot)
        return snapshot

    def _find_checkout_root(self, extract_root: Path) -> Path:
        entries = list(extract_root.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            child_names = {child.name for child in entries[0].iterdir()}
            if {"skills", "plugins", ".claude-plugin", "README.md"} & child_names:
                return entries[0]
        return extract_root

    def _install_unit_type(self, source: Mapping[str, Any]) -> str:
        return str(source.get("install_unit_type", "skill")).strip().lower() or "skill"

    def _content_path(self, source: Mapping[str, Any]) -> str:
        explicit_source_path = str(source.get("source_path", "")).strip()
        if explicit_source_path:
            normalized = explicit_source_path.replace("\\", "/").strip("/")
            if not normalized:
                raise PlatformError(
                    "Marketplace source metadata requires a non-empty source_path.",
                    code="E_RELEASE_CONTENT_INDEX_INVALID",
                    reason="provider_source_fields",
                )
            return normalized
        install_unit_type = self._install_unit_type(source)
        if install_unit_type == "plugin":
            raw = str(source.get("plugin_path", "")).strip()
            field_name = "plugin_path"
        else:
            raw = str(source.get("skill_path", "")).strip()
            field_name = "skill_path"
        normalized = raw.replace("\\", "/").strip("/")
        if not normalized:
            raise PlatformError(
                f"Marketplace {install_unit_type} source metadata requires {field_name}.",
                code="E_RELEASE_CONTENT_INDEX_INVALID",
                reason="provider_source_fields",
            )
        return normalized

    def _resolve_plugin_manifest_path(self, content_root: Path) -> Path | None:
        candidates = [
            content_root / ".codex-plugin" / "plugin.json",
            content_root / ".claude-plugin" / "plugin.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _desired_plugin_manifest_dir(self, target_root_key: str) -> str:
        if target_root_key == "codex_plugins_root":
            return ".codex-plugin"
        return ".claude-plugin"

    def _rewrite_plugin_rel_path(self, rel_path: str, desired_plugin_dir: str) -> str:
        if rel_path.startswith(".claude-plugin/"):
            return rel_path.replace(".claude-plugin/", f"{desired_plugin_dir}/", 1)
        if rel_path.startswith(".codex-plugin/"):
            return rel_path.replace(".codex-plugin/", f"{desired_plugin_dir}/", 1)
        return rel_path

    def _build_manifest_file_specs(
        self,
        *,
        content_root: Path,
        install_unit_type: str,
        desired_plugin_dir: str | None,
    ) -> list[dict[str, str]]:
        if install_unit_type != "plugin" or not desired_plugin_dir:
            return [
                {
                    "asset_name": str(path.relative_to(content_root)).replace("\\", "/"),
                    "target_path": str(path.relative_to(content_root)).replace("\\", "/"),
                }
                for path in sorted(content_root.rglob("*"))
                if path.is_file()
            ]

        selected: dict[str, str] = {}
        for path in sorted(content_root.rglob("*")):
            if not path.is_file():
                continue
            rel_path = str(path.relative_to(content_root)).replace("\\", "/")
            target_path = self._rewrite_plugin_rel_path(rel_path, desired_plugin_dir)
            existing = selected.get(target_path)
            if existing is None:
                selected[target_path] = rel_path
                continue
            preferred_prefix = f"{desired_plugin_dir}/"
            if not existing.startswith(preferred_prefix) and rel_path.startswith(preferred_prefix):
                selected[target_path] = rel_path

        return [
            {"asset_name": asset_name, "target_path": target_path}
            for target_path, asset_name in sorted(selected.items())
        ]

    def _download_remote_snapshot(self, *, repo: str, commit: str, archive_path: Path) -> None:
        try:
            token_res = self.run_cmd_impl(
                ["gh", "auth", "token"],
                check=True,
                capture=True,
                env=gh_subprocess_env(),
            )
        except PlatformError as e:
            raise PlatformError(
                f"Failed to resolve GitHub auth token for marketplace repo '{repo}': {e}",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason="gh_auth_token",
            )

        token = token_res.stdout.strip()
        if not token:
            raise PlatformError(
                f"GitHub auth token for marketplace repo '{repo}' was empty.",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason="gh_auth_token",
            )

        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/tarball/{commit}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ghdp-sync",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                archive_path.write_bytes(resp.read())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
            detail = f" HTTP {e.code}" + (f": {body}" if body else "")
            raise PlatformError(
                f"Failed to download marketplace repo snapshot for {repo}@{commit}.{detail}",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason="marketplace_tarball",
            )
        except urllib.error.URLError as e:
            raise PlatformError(
                f"Failed to download marketplace repo snapshot for {repo}@{commit}: {e}",
                code="E_RELEASE_CONTENT_DOWNLOAD_FAILED",
                reason="marketplace_tarball",
            )


ProviderFactory = Callable[[RunCommand], SyncProvider]

_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}


def register_provider_factory(name: str, factory: ProviderFactory) -> None:
    provider_name = name.strip()
    if not provider_name:
        raise ValueError("Provider name cannot be empty.")
    _PROVIDER_FACTORIES[provider_name] = factory


def get_provider(provider: str, *, run_cmd_impl: RunCommand) -> SyncProvider:
    provider_name = provider.strip() or DEFAULT_PROVIDER
    factory = _PROVIDER_FACTORIES.get(provider_name)
    if factory is None:
        raise unsupported_provider_error(provider_name)
    return factory(run_cmd_impl)


register_provider_factory(DEFAULT_PROVIDER, lambda run_cmd_impl: GitHubReleaseProvider(run_cmd_impl=run_cmd_impl))
register_provider_factory(MARKETPLACE_PROVIDER, lambda run_cmd_impl: MarketplaceRepoProvider(run_cmd_impl=run_cmd_impl))
