"""CLI research + immutable-original range audit; no Session/Run/Report writes.

--real spends search/model budget and retains public original artifacts in the
configured MinIO bucket. This is not a full report/PDF acceptance substitute.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from application.settings import Settings
from domain.documents import ParserConfig
from domain.ports import AdapterError
from domain.research.state import PipelineState
from infrastructure.parser.html import HTML_PARSER_VERSION, HTMLDocumentParser
from infrastructure.parser.pdf import MinerUDocumentParser
from infrastructure.storage.content import MinioContentStore
from scripts.verify_cli_plan import save_record


async def audit(state, artifact_scope):
    settings = Settings.load()
    store = MinioContentStore(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
        secure=settings.minio_secure,
    )
    version = state.run_metadata.config.versions.parser_version
    parser = (
        HTMLDocumentParser(store)
        if version == HTML_PARSER_VERSION
        else MinerUDocumentParser(
            store, settings.mineru_models_dir, timeout_s=state.run_metadata.config.timeouts_s.parser
        )
    )
    verified, parsed_sources = [], {}
    try:
        for evidence in state.evidence.values():
            source = state.sources[evidence.source_id]
            if (
                source.data_classification != "public"
                or not source.content_object_key
                or not source.content_object_key.startswith(f"research-content/{artifact_scope}/")
            ):
                raise ValueError("Probe cannot audit a different/private content scope")
            if source.source_id not in parsed_sources:
                reference = await store.head(source.content_object_key)
                if (
                    reference.sha256 != source.content_hash
                    or source.content_hash != evidence.content_hash
                ):
                    raise ValueError("Source/Evidence original hash differs")
                # Parser reads/hash-checks the stored original, not the search
                # result or a trusted copy of the model's quote.
                parsed_sources[source.source_id] = await parser.parse(
                    reference, ParserConfig(parser_version=version)
                )
            parsed = parsed_sources[source.source_id]
            blocks = [block for block in parsed.blocks if block.location == evidence.location]
            if not blocks:
                raise ValueError("Evidence location is not in the original parser output")
            valid = False
            for block in blocks:
                content = "\n".join(
                    [block.content, *([block.caption] if block.caption else []), *block.notes]
                )
                valid |= (
                    evidence.quote_or_raw_content == content
                    if block.type in {"table", "formula"}
                    else evidence.quote_or_raw_content in block.content
                )
            if not valid:
                raise ValueError("Evidence quote is not a valid original range")
            verified.append(
                {
                    "evidence_id": evidence.evidence_id,
                    "source_id": source.source_id,
                    "url": source.canonical_url,
                    "hash": source.content_hash,
                    "location": evidence.location.model_dump(mode="json"),
                    "original_quote_verified": True,
                }
            )
    finally:
        await parser.close()
        await store.close()
    return verified


async def verify(args):
    initial = PipelineState.model_validate_json(Path(args.state).read_text())
    if initial.phase != "research":
        raise ValueError("Probe requires a formal research state")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cli",
        "phase",
        "research",
        "--state",
        args.state,
        "--json",
        *(["--real"] if args.real else []),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=initial.run_metadata.config.limits.deadline_s + 10
        )
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    result = json.loads(stdout)
    verified, unmet = [], []
    if process.returncode == 0:
        post = PipelineState.model_validate(result["state"])
        assert (
            post.phase == initial.phase and post.run_metadata.config == initial.run_metadata.config
        )
        assert post.run_metadata.budget_used == initial.run_metadata.budget_used
        assert post.research_brief == initial.research_brief and post.final_report is None
        if args.real:
            try:
                verified = await audit(post, result["debug_usage"].get("artifact_scope"))
            except AdapterError as exc:
                unmet.append(f"Original content audit failed: {exc.code}")
            except ValueError:
                unmet.append("Original content audit failed: invalid hash, scope, or range")
            if not verified:
                unmet.append("No original Evidence was produced")
            if not any(source.source_type == "paper" for source in post.sources.values()):
                unmet.append("No real paper/PDF source; T028 remains incomplete")
    else:
        unmet.append("CLI research failed")
    record = {
        "scope": "isolated_cli_research",
        "dependency_mode": "real" if args.real else "fake",
        "persisted_run": False,
        "result": result,
        "verified_evidence": verified,
        "unmet_acceptance": unmet,
    }
    if args.record:
        save_record(args.record, record)
    print(json.dumps(record, ensure_ascii=False))
    return process.returncode or (1 if args.real and unmet else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--record", help="exclusive new public evidence JSON file")
    return asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
