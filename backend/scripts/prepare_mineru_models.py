"""Explicit download of fixed public MinerU weights; never called during parse.

Use --root for a dedicated, empty model root. Existing unmanifested payload is
rejected rather than overwritten. The pinned SDK writes its own readiness
markers after verifying the downloaded snapshot; this script does not forge them.
"""

import argparse
import hashlib
import importlib.metadata
import json
import ssl
from pathlib import Path
from unittest.mock import patch

from infrastructure.parser.mineru_output import (
    MINERU_PACKAGE_VERSION,
    MINERU_PARSER_VERSION,
    MODEL_REVISIONS,
)


def prepare(root, *, tls12=False):
    if importlib.metadata.version("mineru") != MINERU_PACKAGE_VERSION:
        raise ValueError("MinerU package version differs from frozen runtime")
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new empty root; existing model payload must not be overwritten")
    root.mkdir(parents=True, exist_ok=True)
    import httpx
    from huggingface_hub import HfApi, set_client_factory, snapshot_download
    from huggingface_hub.utils import filter_repo_objects
    from mineru.config import config
    from mineru.model import download
    from mineru.model.registry import MINERU_2_5_PRO_2605_1_2B_GGUF, MINERU_4_MODELS_TORCH

    context = ssl.create_default_context()
    if tls12:
        # Optional environment diagnostic: keep certificate/hostname validation.
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    set_client_factory(lambda: httpx.Client(verify=context, follow_redirects=True, timeout=60))
    config.model.base_dir = str(root)
    config.model.source = "local"
    records = []

    def pinned_snapshot(source, repo, patterns):
        if source != "huggingface":
            raise ValueError("Only the explicitly pinned public provider is allowed")
        repo_id = repo.repos[source]
        revision = MODEL_REVISIONS[repo_id]
        expected = list(
            filter_repo_objects(
                HfApi().list_repo_files(repo_id, revision=revision), allow_patterns=patterns
            )
        )
        if not expected:
            raise ValueError("Pinned snapshot has no required files")
        target = snapshot_download(
            repo_id,
            revision=revision,
            local_dir=repo.local_dir(),
            allow_patterns=patterns,
            max_workers=2,
        )
        files = []
        for name in expected:
            path = Path(target) / name
            if not path.is_file() or not path.stat().st_size:
                raise ValueError("Pinned snapshot is incomplete")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            files.append(
                {
                    "path": f"{repo.local_name}/{name}",
                    "size": path.stat().st_size,
                    "sha256": digest.hexdigest(),
                }
            )
        records.append({"repo": repo_id, "revision": revision, "files": files})
        return target

    # Narrow, setup-only revision injection: MinerU's pinned download function
    # otherwise consults main. Its lock/path checks and completion logic stay intact.
    with patch.object(download, "_snapshot_download", pinned_snapshot):
        for repo in (MINERU_4_MODELS_TORCH, MINERU_2_5_PRO_2605_1_2B_GGUF):
            download.download_model_repo(repo, source="huggingface")
            if not download.verify_model_repo(repo).ready:
                raise ValueError("MinerU local readiness check failed")
    manifest = {
        "parser_version": MINERU_PARSER_VERSION,
        "package_version": MINERU_PACKAGE_VERSION,
        "small_backend": "torch",
        "vlm_engine": "llama-cpp",
        "repositories": records,
    }
    with (root / "dr4a-models.json").open("x") as stream:
        json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
    print(json.dumps({"status": "ready", "root": str(root), "repositories": len(records)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument(
        "--tls12", action="store_true", help="TLS 1.2 with certificate checks retained"
    )
    args = parser.parse_args()
    prepare(args.root, tls12=args.tls12)


if __name__ == "__main__":
    main()
