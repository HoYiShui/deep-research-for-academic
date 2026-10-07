"""Real PDFium input preflight; explicitly not MinerU inference acceptance."""

import pytest

from domain.documents import ParserConfig
from domain.ports import AdapterError
from infrastructure.parser.mineru_output import MINERU_PARSER_VERSION
from infrastructure.parser.mineru_worker import parse_local
from infrastructure.parser.pdf import MinerUDocumentParser
from tests.contract.test_document_parser import reference


def test_bad_pdf_rejected_before_reading_any_model_root(tmp_path):
    pytest.importorskip("mineru")
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"%PDF-1.7\nnot a document")
    with pytest.raises(AdapterError, match="invalid_document"):
        parse_local(
            path, tmp_path / "no-models", ParserConfig(parser_version=MINERU_PARSER_VERSION)
        )


def test_real_pdf_page_limit_precedes_model_access(tmp_path):
    pytest.importorskip("mineru")
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    path = tmp_path / "two-pages.pdf"
    canvas = reportlab.Canvas(str(path))
    for index in range(2):
        canvas.drawString(20, 20, f"Original page {index + 1}")
        canvas.showPage()
    canvas.save()
    with pytest.raises(AdapterError, match="resource_limit"):
        parse_local(
            path,
            tmp_path / "no-models",
            ParserConfig(
                parser_version=MINERU_PARSER_VERSION,
                max_pages=1,
            ),
        )


async def test_owned_subprocess_rejects_real_invalid_pdf_and_releases_process(tmp_path):
    pytest.importorskip("mineru")
    body = b"%PDF-1.7\nnot a document"

    class Content:
        async def get(self, key):
            async def chunks():
                yield body

            return chunks()

    parser = MinerUDocumentParser(Content(), tmp_path / "no-models", timeout_s=20)
    try:
        with pytest.raises(AdapterError, match="invalid_document"):
            await parser.parse(
                reference(body, "application/pdf"),
                ParserConfig(
                    parser_version=MINERU_PARSER_VERSION,
                ),
            )
        assert not parser._processes
    finally:
        await parser.close()
