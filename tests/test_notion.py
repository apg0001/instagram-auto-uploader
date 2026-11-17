import pytest

from uploader.notion import CHUNK, page_id_from, plain, rich


def test_page_id_from_url_and_id():
    pid = "0123456789abcdef0123456789abcdef"
    assert page_id_from(f"https://www.notion.so/my/Page-{pid}?pvs=4") == pid
    assert page_id_from("01234567-89ab-cdef-0123-456789abcdef") == pid
    with pytest.raises(ValueError):
        page_id_from("https://www.notion.so/no-id")


def test_rich_text_chunks_round_trip():
    text = "가" * (CHUNK * 2 + 5)
    chunks = rich(text)
    assert len(chunks) == 3
    assert plain([{"plain_text": c["text"]["content"]} for c in chunks]) == text
