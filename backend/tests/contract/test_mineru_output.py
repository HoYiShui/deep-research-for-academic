"""Controlled MinerU renderer boundary, not real PDF inference acceptance."""

import pytest

from domain.documents import ParserConfig
from domain.ports import AdapterError
from infrastructure.parser.mineru_output import MINERU_PARSER_VERSION, content_list_document


def convert(items, **kwargs):
    return content_list_document(
        items,
        input_hash="a" * 64,
        page_count=kwargs.pop("page_count", 3),
        config=ParserConfig(parser_version=MINERU_PARSER_VERSION, **kwargs),
    )


def test_preserves_whole_atomic_table_formula_and_original_pdf_page():
    table = "<table><tr><td>score</td><td>0</td></tr></table>"
    result = convert(
        [
            {"type": "text", "text": "3 Results", "text_level": 1, "page_idx": 1},
            {
                "type": "table",
                "table_body": table,
                "table_caption": ["Table 1"],
                "table_footnote": ["Three seeds; ± is SD"],
                "page_idx": 1,
            },
            {"type": "equation", "text": "x = 0", "page_idx": 2},
        ]
    )
    assert result.blocks[1].content == table
    assert result.blocks[1].caption == "Table 1"
    assert result.blocks[1].notes == ["Three seeds; ± is SD"]
    assert result.blocks[1].location.page_start == 2
    assert result.blocks[1].location.section == "3 Results"
    assert result.blocks[2].type == "formula" and result.blocks[2].content == "x = 0"
    assert result.blocks[2].location.page_start == 3


@pytest.mark.parametrize("kind", ["header", "footer", "aside_text", "page_footnote", "page_number"])
def test_actual_mineru_auxiliary_type_names_preserve_original_text(kind):
    result = convert([{"type": kind, "text": "Actual auxiliary text", "page_idx": 0}])
    assert result.blocks[0].content == "Actual auxiliary text"
    assert result.blocks[0].location.page_start == 1


@pytest.mark.parametrize("page", [None, True, -1, 3, "0"])
def test_does_not_guess_or_coerce_page_index(page):
    with pytest.raises(AdapterError, match="parser_output_invalid"):
        convert([{"type": "text", "text": "Original", "page_idx": page}])


@pytest.mark.parametrize(
    "item",
    [
        {"type": "table", "img_path": "table.jpg"},
        {"type": "equation", "img_path": "formula.jpg"},
    ],
)
def test_image_only_atomic_content_is_not_successful_fake_text(item):
    with pytest.raises(AdapterError, match="parser_output_incomplete"):
        convert([item | {"page_idx": 0}])


def test_empty_and_resource_limits_fail_instead_of_truncating():
    with pytest.raises(AdapterError, match="parse_empty"):
        convert([])
    with pytest.raises(AdapterError, match="parse_empty"):
        convert([{"type": "text", "text": " ", "page_idx": 0}])
    with pytest.raises(AdapterError, match="resource_limit"):
        convert([], max_pages=1)
    with pytest.raises(AdapterError, match="resource_limit"):
        convert([{"type": "text", "text": "x" * 1000, "page_idx": 0}], max_content_bytes=100)


def test_out_of_order_pages_cannot_reassign_section_context():
    with pytest.raises(AdapterError, match="parser_output_invalid"):
        convert(
            [
                {"type": "text", "text": "Later", "page_idx": 2},
                {"type": "text", "text": "Earlier", "page_idx": 0},
            ]
        )
