"""Fail-closed inspection for model-proposed and staged delivery artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any


class ArtifactSecurityError(ValueError):
    """A proposed artifact violated the delivery security policy."""


def protected_path_reason(relative: str) -> str | None:
    """Return why a normalized repository path must stay outside model control.

    The same predicate protects both mutation and model-context boundaries so a
    tracked file cannot become readable merely because it is not being edited.
    """
    path = PurePosixPath(relative)
    lower_parts = tuple(part.casefold() for part in path.parts)
    if not lower_parts:
        return "protected repository path"
    basename = lower_parts[-1]
    top = lower_parts[0]
    if ".git" in lower_parts:
        return "Git internals are protected"
    if basename.startswith(".env"):
        return "environment files are protected"
    credential_directories = {
        ".aws",
        ".azure",
        ".credentials",
        ".docker",
        ".gcp",
        ".gnupg",
        ".kube",
        ".secrets",
        ".ssh",
        "credential",
        "credentials",
        "secret",
        "secrets",
    }
    credential_files = {
        ".authinfo",
        ".authinfo.gpg",
        ".boto",
        ".curlrc",
        ".dockerconfigjson",
        ".git-credentials",
        ".gitconfig",
        ".htpasswd",
        ".my.cnf",
        ".netrc",
        ".npmrc",
        ".pgpass",
        ".pypirc",
        ".s3cfg",
        ".vault-token",
        ".wgetrc",
        "_netrc",
        "application_default_credentials.json",
        "auth.json",
        "client_secret.json",
        "credentials.json",
        "kubeconfig",
        "secret.json",
        "secrets.json",
        "service-account-key.json",
        "service-account.json",
        "service_account_key.json",
        "service_account.json",
    }
    credential_config_tree = any(
        lower_parts[index : index + 2] in {
            (".config", "gcloud"),
            (".config", "gh"),
            (".config", "hub"),
        }
        for index in range(len(lower_parts) - 1)
    )
    if (
        any(part in credential_directories for part in lower_parts)
        or basename in credential_files
        or credential_config_tree
        or basename.startswith("client_secret_")
        or (basename.startswith("firebase-adminsdk-") and basename.endswith(".json"))
        or basename.endswith((".credentials", ".secret", ".secrets", ".tfstate"))
        or ".tfstate." in basename
    ):
        return "credential and secret configuration is protected"
    if top in {".buildkite", ".circleci", ".github", ".gitlab"}:
        return "CI workflow definitions are protected"
    if top in {".agents", ".claude", ".codex", ".mondayos"} or basename in {
        "agents.md",
        "claude.md",
        "skill.md",
        ".cursorrules",
        ".mcp.json",
        "copilot-instructions.md",
    }:
        return "AI/controller instruction files are protected"
    if basename in {".gitmodules", ".gitattributes"}:
        return "Git configuration files are protected"
    if basename in {
        ".pytest.ini",
        ".pytest.toml",
        "conftest.py",
        "pytest.ini",
        "pytest.toml",
        "tox.ini",
    }:
        return "test harness configuration is protected"
    if top in {
        ".aws",
        ".azure",
        ".gcp",
        "charts",
        "deploy",
        "deployment",
        "helm",
        "infra",
        "k8s",
        "kubernetes",
        "service",
        "services",
        "terraform",
    }:
        return "deployment/service definitions are protected"
    if basename.endswith((".service", ".timer", ".socket", ".plist")) or basename in {
        "dockerfile",
        "procfile",
        ".drone.yml",
        ".travis.yml",
        "appveyor.yml",
        "azure-pipelines.yml",
        "bitbucket-pipelines.yml",
        "buildspec.yml",
        "buildspec.yaml",
        "cloudbuild.yml",
        "cloudbuild.yaml",
        "firebase.json",
        "fly.toml",
        "jenkinsfile",
        "netlify.toml",
        "railway.json",
        "render.yaml",
        "render.yml",
        "serverless.yaml",
        "serverless.yml",
        "vercel.json",
    } or basename.startswith(("docker-compose.", "compose.")):
        return "deployment/service definitions are protected"
    if basename.endswith((".pem", ".key", ".p12", ".pfx")) or basename in {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }:
        return "key material is protected"
    manifests = {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pipfile",
        "pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
        "gemfile",
        "gemfile.lock",
        "composer.json",
        "composer.lock",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gradle.lockfile",
    }
    if basename in manifests or (
        basename.startswith("requirements") and basename.endswith((".txt", ".in"))
    ):
        return "dependency manifests and locks are protected"
    return None


def is_protected_path(relative: str) -> bool:
    """Whether a normalized path is excluded from delivery mutation and context."""
    return protected_path_reason(relative) is not None


_PATCH_PATH = re.compile(r"^diff --git a/(.+) b/(.+)$")
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private key",
        re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*(?:PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----"),
    ),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "source-control token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,})\b"),
    ),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Telegram bot token", re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[aboprs]-[A-Za-z0-9-]{20,}\b")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b")),
    (
        "JSON Web Token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
    ),
    (
        "credential-bearing database URL",
        re.compile(
            r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://"
            r"[^\s:/@]+:[^\s/@]+@"
        ),
    ),
    (
        "authorization credential",
        re.compile(r"(?i)\b(?:authorization\s*[:=]\s*)?bearer\s+[A-Za-z0-9._~+/-]{12,}={0,2}"),
    ),
    (
        "assigned credential",
        re.compile(
            r'''(?ix)
            ["']?
            (?:password|passwd|token|secret|api[_-]?key|access[_-]?token|
               refresh[_-]?token|client[_-]?secret|aws[_-]?secret[_-]?access[_-]?key)
            ["']?\s*(?:=|:)\s*
            (?:"[^"\r\n]{8,}"|'[^'\r\n]{8,}'|[A-Za-z0-9_+/-]{12,})
            '''
        ),
    ),
)


def proposed_paths(patch: str) -> tuple[str, ...]:
    """Return normalized paths named by a text-only Git patch.

    This is an early screen, not a parser of Git's patch grammar.  Git still
    performs its own strict ``apply --check``, and the resulting filesystem is
    independently inspected before staging.
    """
    if not isinstance(patch, str) or not patch.strip():
        raise ArtifactSecurityError("the builder returned an empty patch")
    if "\x00" in patch:
        raise ArtifactSecurityError("patch contains a NUL byte")
    if "GIT binary patch" in patch or "Binary files " in patch:
        raise ArtifactSecurityError("binary patches are not permitted")

    paths: set[str] = set()
    headers = 0
    for line in patch.splitlines():
        match = _PATCH_PATH.fullmatch(line)
        if not match:
            continue
        headers += 1
        for raw in match.groups():
            normalized = _normal_patch_path(raw)
            paths.add(normalized)
    if headers == 0 or not paths:
        raise ArtifactSecurityError("patch contains no valid Git file headers")
    if len(paths) > 100:
        raise ArtifactSecurityError("patch changes more than 100 paths")
    return tuple(sorted(paths))


def scan_staged_diff(diff: str) -> None:
    """Reject credential material anywhere in a diff sent to a model.

    Deleted and context lines leave the machine alongside additions during the
    independent review.  Scanning only new content would therefore disclose a
    tracked credential precisely when a candidate attempted to remove it.
    """
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(diff):
            raise ArtifactSecurityError(f"staged diff appears to contain a {label}")


def verification_digest(verification: list[dict[str, Any]]) -> str:
    payload = json.dumps(verification, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def artifact_digest(
    *,
    base_sha: str,
    objective: str,
    diff: str,
    verification_sha256: str,
    project_name: str = "",
    repository: str = "",
) -> tuple[str, str]:
    """Bind review authorization to the exact target, base, task, diff, and checks."""
    diff_sha256 = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    envelope = json.dumps(
        {
            "base_sha": base_sha,
            "objective": objective,
            "diff_sha256": diff_sha256,
            "project_name": project_name,
            "repository": repository,
            "verification_sha256": verification_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return diff_sha256, hashlib.sha256(envelope.encode("utf-8")).hexdigest()


def _normal_patch_path(raw: str) -> str:
    # Quoted/escaped patch paths require a full Git unquoter.  Rejecting them
    # keeps the controller's pre-apply interpretation identical to its policy.
    if not raw or raw.startswith('"') or "\\" in raw or "\t" in raw:
        raise ArtifactSecurityError("quoted or escaped patch paths are not permitted")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw != path.as_posix():
        raise ArtifactSecurityError(f"unsafe patch path: {raw!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactSecurityError(f"unsafe patch path: {raw!r}")
    return path.as_posix()
