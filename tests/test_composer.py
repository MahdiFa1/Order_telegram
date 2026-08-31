"""Message composition: no forward header, correct entity offsets."""

from __future__ import annotations

import pytest

from app.database.models import OrderSourceMessage
from app.telegram.composer import compose, shift_entities, utf16_length
from app.utils.enums import ContentType

pytestmark = pytest.mark.asyncio


def source_message(**fields) -> OrderSourceMessage:
    defaults = {
        "chat_id": -100,
        "message_id": 1,
        "content_type": ContentType.TEXT.value,
        "file_id": None,
        "text": None,
        "caption": None,
        "entities": None,
        "caption_entities": None,
        "has_spoiler": False,
        "position": 0,
        "media_group_id": None,
    }
    defaults.update(fields)
    return OrderSourceMessage(**defaults)


async def test_utf16_length_counts_surrogate_pairs():
    assert utf16_length("order15\n\n") == 9
    # An emoji outside the BMP occupies two UTF-16 code units.
    assert utf16_length("🎉") == 2
    assert utf16_length("سلام") == 4


async def test_text_order_gets_the_number_prepended():
    composed = compose(
        "order15", [source_message(text="Apple ID\nUS\n100$", content_type="TEXT")]
    )
    assert len(composed.operations) == 1
    operation = composed.operations[0]
    assert operation.kind == "text"
    assert operation.payload["text"] == "order15\n\nApple ID\nUS\n100$"


async def test_entities_are_shifted_by_the_header_length():
    entities = [{"type": "bold", "offset": 0, "length": 8}]
    composed = compose(
        "order15",
        [source_message(text="Apple ID\nUS", entities=entities, content_type="TEXT")],
    )
    shifted = composed.operations[0].payload["entities"]
    # "order15\n\n" is 9 UTF-16 units, so the bold run moves from 0 to 9.
    assert shifted == [{"type": "bold", "offset": 9, "length": 8}]
    # The stored entity list is never mutated in place.
    assert entities[0]["offset"] == 0


async def test_entity_shift_accounts_for_a_non_ascii_prefix():
    composed = compose(
        "🎉7",
        [source_message(text="body", entities=[{"type": "bold", "offset": 0, "length": 4}])],
    )
    # "🎉7\n\n" is 2 + 1 + 2 = 5 UTF-16 code units.
    assert composed.operations[0].payload["entities"][0]["offset"] == 5


async def test_single_photo_uses_a_caption_not_a_forward():
    composed = compose(
        "order3",
        [
            source_message(
                content_type=ContentType.PHOTO.value, file_id="abc", caption="Apple ID"
            )
        ],
    )
    assert len(composed.operations) == 1
    operation = composed.operations[0]
    assert operation.kind == "media"
    assert operation.payload["caption"] == "order3\n\nApple ID"
    assert operation.payload["file_id"] == "abc"


async def test_album_is_rebuilt_as_one_media_group_with_a_single_caption():
    messages = [
        source_message(
            message_id=index,
            content_type=ContentType.PHOTO.value,
            file_id=f"file{index}",
            caption="Apple ID" if index == 0 else None,
            media_group_id="mg",
            position=index,
        )
        for index in range(4)
    ]
    composed = compose("order9", messages)
    assert len(composed.operations) == 1
    operation = composed.operations[0]
    assert operation.kind == "album"
    items = operation.payload["media"]
    assert len(items) == 4
    assert items[0]["caption"] == "order9\n\nApple ID"
    assert all("caption" not in item for item in items[1:])
    assert [item["media"] for item in items] == ["file0", "file1", "file2", "file3"]


async def test_mixed_album_of_photo_and_video_is_preserved():
    messages = [
        source_message(
            message_id=0, content_type="PHOTO", file_id="p", caption="c", media_group_id="m"
        ),
        source_message(message_id=1, content_type="VIDEO", file_id="v", media_group_id="m"),
    ]
    composed = compose("order2", messages)
    items = composed.operations[0].payload["media"]
    assert [item["type"] for item in items] == ["photo", "video"]


async def test_unsupported_type_falls_back_to_header_plus_copy():
    """copyMessage is still used — never forwardMessage — so no header appears."""
    composed = compose(
        "order4",
        [source_message(content_type=ContentType.STICKER.value, file_id="s", message_id=77)],
        source_chat_id=-500,
    )
    kinds = [operation.kind for operation in composed.operations]
    assert kinds == ["text", "copy"]
    assert composed.operations[0].payload["text"] == "order4"
    assert composed.operations[1].payload == {"from_chat_id": -500, "message_id": 77}


async def test_oversized_caption_moves_the_number_to_its_own_message():
    composed = compose(
        "order5",
        [source_message(content_type="PHOTO", file_id="f", caption="x" * 1024)],
    )
    kinds = [operation.kind for operation in composed.operations]
    assert kinds == ["text", "media"]
    # The original caption survives untruncated.
    assert composed.operations[1].payload["caption"] == "x" * 1024


async def test_oversized_text_is_split_rather_than_truncated_over_the_header():
    composed = compose("order6", [source_message(text="y" * 4096)])
    assert [operation.kind for operation in composed.operations] == ["text", "text"]
    assert composed.operations[0].payload["text"] == "order6"


async def test_empty_source_produces_no_operations():
    assert compose("order7", []).is_empty


async def test_shift_entities_handles_none():
    assert shift_entities(None, 5) is None
    assert shift_entities([], 5) is None
