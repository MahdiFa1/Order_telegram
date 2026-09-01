"""Order-number gate, source reactions, attachments, appended text and store."""

from __future__ import annotations

import pytest

from app.database.engine import session_scope
from app.database.repositories import (
    AttachmentRepository,
    RejectedMessageRepository,
    ResultConfigRepository,
    SettingRepository,
    SourceReactionRepository,
    WooCommerceRepository,
)
from app.utils.enums import (
    DispatchStatus,
    OrderStatus,
    ResultContentMode,
    SettingKey,
    SignalKey,
    SourceReactionStage,
)
from tests.conftest import (
    OPERATOR_ID,
    SOURCE_CHAT_ID,
    STRANGER_ID,
    SUCCESS_CHAT_ID,
    WORK_GROUP_CHAT_ID,
    configure_acknowledgement,
    configure_rule,
)
from tests.helpers import (
    deliver_order,
    get_order,
    operator_reacts,
    operator_replies,
    photo_payload,
    primary_work_group_message,
    text_payload,
)

pytestmark = pytest.mark.asyncio


async def _pending(services) -> tuple[int, int, int]:
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "New Order"))
    chat_id, message_id = await primary_work_group_message(order_id, WORK_GROUP_CHAT_ID)
    return order_id, chat_id, message_id


# ===========================================================================
# The reported bug: operator photos never reached the result destination
# ===========================================================================
async def test_operator_photos_reach_the_success_destination(destinations):
    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id, _chat, _message = await _pending(services)
    reply = photo_payload(WORK_GROUP_CHAT_ID, file_id="operator-shot-1")
    await operator_replies(services, order_id, reply, OPERATOR_ID)

    assert (await get_order(order_id)).status == OrderStatus.SUCCESS
    sent = services.gateway.messages_in(SUCCESS_CHAT_ID)
    # The order itself, plus the operator's photo.
    assert len(sent) == 2
    assert any(m.payload.get("file_id") == "operator-shot-1" for m in sent)


async def test_operator_photos_reach_the_failure_destination(destinations):
    from tests.conftest import FAILURE_CHAT_ID

    services = destinations
    await configure_rule(OrderStatus.FAILED, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.FAILED, enabled=False)

    order_id, _chat, _message = await _pending(services)
    await operator_replies(
        services, order_id, photo_payload(WORK_GROUP_CHAT_ID, file_id="fail-shot"), OPERATOR_ID
    )

    sent = services.gateway.messages_in(FAILURE_CHAT_ID)
    assert any(m.payload.get("file_id") == "fail-shot" for m in sent)


async def test_several_operator_photos_are_all_forwarded(destinations):
    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REPLY_TEXT,), texts=("done",)
    )
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id, _chat, _message = await _pending(services)
    # Two photos first, then the text that finalises the order.
    for index in range(2):
        await operator_replies(
            services, order_id, photo_payload(WORK_GROUP_CHAT_ID, file_id=f"shot-{index}"), OPERATOR_ID
        )
    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "done"), OPERATOR_ID
    )

    sent = services.gateway.messages_in(SUCCESS_CHAT_ID)
    file_ids = {m.payload.get("media") or m.payload.get("file_id") for m in sent}
    assert {"shot-0", "shot-1"} <= file_ids


async def test_only_file_ids_are_stored_never_media(destinations):
    """The database must not grow with image data."""
    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    order_id, _chat, _message = await _pending(services)
    await operator_replies(
        services, order_id, photo_payload(WORK_GROUP_CHAT_ID, file_id="abc123"), OPERATOR_ID
    )

    async with session_scope() as session:
        rows = await AttachmentRepository(session).list_for_order(order_id)
    assert [r.file_id for r in rows] == ["abc123"]
    assert all(len(r.file_id) < 200 for r in rows)
    assert not hasattr(rows[0], "data"), "no binary column should exist"


async def test_a_stranger_reply_does_not_attach_media(destinations):
    from app.rules.extractor import extract_from_reply

    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    order_id, _chat, _message = await _pending(services)

    payload = photo_payload(WORK_GROUP_CHAT_ID, file_id="stranger")
    async with session_scope() as session:
        signals = await extract_from_reply(session, payload, STRANGER_ID)
    assert signals == []

    async with session_scope() as session:
        rows = await AttachmentRepository(session).list_for_order(order_id)
    assert rows == []


async def test_attachments_only_mode_skips_the_order_body(destinations):
    services = destinations
    async with session_scope() as session:
        await SettingRepository(session).set(
            SettingKey.RESULT_CONTENT_MODE, ResultContentMode.ATTACHMENTS_ONLY.value
        )
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id, _chat, _message = await _pending(services)
    await operator_replies(
        services, order_id, photo_payload(WORK_GROUP_CHAT_ID, file_id="only-this"), OPERATOR_ID
    )

    sent = services.gateway.messages_in(SUCCESS_CHAT_ID)
    assert len(sent) == 1
    assert sent[0].payload.get("file_id") == "only-this"


async def test_attachments_only_falls_back_when_there_are_none(destinations):
    """A reaction-finalised order has no operator media; send the order."""
    services = destinations
    async with session_scope() as session:
        await SettingRepository(session).set(
            SettingKey.RESULT_CONTENT_MODE, ResultContentMode.ATTACHMENTS_ONLY.value
        )
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id, chat_id, message_id = await _pending(services)
    await operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID)

    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1


# ===========================================================================
# Text appended in the result destination
# ===========================================================================
async def test_appended_text_is_added_to_the_result(destinations):
    services = destinations
    async with session_scope() as session:
        await ResultConfigRepository(session).update(
            OrderStatus.SUCCESS,
            append_text_enabled=True,
            append_text="✅سفارش با موفقیت انجام شد",
        )
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_TEXT,), texts=("done",))
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id, _chat, _message = await _pending(services)
    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "done"), OPERATOR_ID
    )

    body = services.gateway.messages_in(SUCCESS_CHAT_ID)[0].text
    assert body.endswith("✅سفارش با موفقیت انجام شد")
    assert "New Order" in body


async def test_appended_text_can_be_switched_off(destinations):
    services = destinations
    async with session_scope() as session:
        await ResultConfigRepository(session).update(
            OrderStatus.SUCCESS, append_text_enabled=False, append_text="ignored"
        )
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_TEXT,), texts=("done",))
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id, _chat, _message = await _pending(services)
    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "done"), OPERATOR_ID
    )
    assert "ignored" not in services.gateway.messages_in(SUCCESS_CHAT_ID)[0].text


async def test_each_status_appends_its_own_text(destinations):
    from tests.conftest import FAILURE_CHAT_ID

    services = destinations
    async with session_scope() as session:
        repo = ResultConfigRepository(session)
        await repo.update(OrderStatus.SUCCESS, append_text_enabled=True, append_text="OK-TEXT")
        await repo.update(OrderStatus.FAILED, append_text_enabled=True, append_text="BAD-TEXT")
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    await configure_acknowledgement(OrderStatus.FAILED, enabled=False)

    ok_id, _c, _m = await _pending(services)
    await services.finalizer.manual_override(ok_id, OrderStatus.SUCCESS, 1000)
    bad_id, _c, _m = await _pending(services)
    await services.finalizer.manual_override(bad_id, OrderStatus.FAILED, 1000)

    assert "OK-TEXT" in services.gateway.messages_in(SUCCESS_CHAT_ID)[0].text
    assert "BAD-TEXT" in services.gateway.messages_in(FAILURE_CHAT_ID)[0].text


# ===========================================================================
# Store order number: parsed from the last line, and enforced
# ===========================================================================
async def _require_order_number(length: int = 7, delete: bool = True) -> None:
    async with session_scope() as session:
        repo = SettingRepository(session)
        await repo.set(SettingKey.ORDER_NUMBER_ENABLED, "true")
        await repo.set(SettingKey.ORDER_NUMBER_LENGTH, str(length))
        await repo.set(
            SettingKey.ORDER_NUMBER_DELETE_INVALID, "true" if delete else "false"
        )
        await repo.set(
            SettingKey.ORDER_NUMBER_REJECT_MESSAGE,
            "{name} عزیز، شماره سفارش قرار نگرفته یا اشتباه است.",
        )


async def test_a_valid_order_number_is_stored_on_the_order(wired):
    services = wired
    await _require_order_number()
    order_id = await deliver_order(
        services, text_payload(SOURCE_CHAT_ID, "Apple ID\nUS\n100$\n1234567")
    )
    assert (await get_order(order_id)).source_order_number == "1234567"


async def test_persian_digits_are_accepted(wired):
    services = wired
    await _require_order_number()
    order_id = await deliver_order(
        services, text_payload(SOURCE_CHAT_ID, "Apple ID\n۱۲۳۴۵۶۷")
    )
    assert (await get_order(order_id)).source_order_number == "1234567"


async def test_a_missing_order_number_stops_the_order(wired):
    """Nothing is created, nothing reaches the work group, the author is told."""
    services = wired
    await _require_order_number()

    payload = text_payload(SOURCE_CHAT_ID, "Apple ID\nUS\n100$")
    payload.extra = {"author_user_id": 42, "author_name": "مهدی"}
    result = await services.orders.ingest(payload)

    assert result.rejected is True
    assert result.order_id is None
    assert "مهدی" in result.rejection_text

    await services.orders.deliver_rejection(payload, result.rejection_text)
    assert services.gateway.messages_in(WORK_GROUP_CHAT_ID) == []
    assert services.gateway.replies[0][0] == SOURCE_CHAT_ID
    assert "مهدی عزیز" in services.gateway.replies[0][2]
    assert services.gateway.deleted == [(SOURCE_CHAT_ID, payload.message_id)]


async def test_a_wrong_length_order_number_is_refused(wired):
    services = wired
    await _require_order_number(length=7)
    payload = text_payload(SOURCE_CHAT_ID, "Apple ID\n123456")
    assert (await services.orders.ingest(payload)).rejected is True


async def test_the_length_is_configurable(wired):
    services = wired
    await _require_order_number(length=8)
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple\n12345678"))
    assert (await get_order(order_id)).source_order_number == "12345678"

    assert (await services.orders.ingest(text_payload(SOURCE_CHAT_ID, "Apple\n1234567"))).rejected


async def test_a_refused_post_keeps_its_text_for_the_audit(wired):
    services = wired
    await _require_order_number()
    payload = text_payload(SOURCE_CHAT_ID, "Apple ID\nno number here")
    await services.orders.ingest(payload)

    async with session_scope() as session:
        rows = await RejectedMessageRepository(session).recent()
    assert len(rows) == 1
    assert rows[0].content == "Apple ID\nno number here"
    assert rows[0].deleted is True


async def test_a_refused_post_consumes_no_order_number(wired):
    """The daily counter must not skip a number for a rejected post."""
    services = wired
    await _require_order_number()
    await services.orders.ingest(text_payload(SOURCE_CHAT_ID, "bad\nxx"))
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "good\n1234567"))
    assert (await get_order(order_id)).display_number == "order1"


async def test_the_gate_is_off_by_default(wired):
    services = wired
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "no number"))
    assert order_id is not None
    assert len(services.gateway.messages_in(WORK_GROUP_CHAT_ID)) == 1


async def test_operator_text_cannot_disturb_the_stored_number(destinations):
    """`420x2✅` in a work-group reply must not change the order number."""
    services = destinations
    await _require_order_number()
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_TEXT,), texts=("✅",))
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id = await deliver_order(
        services, text_payload(SOURCE_CHAT_ID, "Apple ID\n1234567")
    )
    await primary_work_group_message(order_id, WORK_GROUP_CHAT_ID)
    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "420x2✅"), OPERATOR_ID
    )

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    # The number was read once, at intake, from the source message.
    assert order.source_order_number == "1234567"


# ===========================================================================
# Reactions on the original source message
# ===========================================================================
async def _configure_source_stages(**stages: str) -> None:
    async with session_scope() as session:
        repo = SourceReactionRepository(session)
        for stage_name, emoji in stages.items():
            await repo.update_config(
                SourceReactionStage(stage_name.upper()), enabled=True, reaction_value=emoji
            )


async def test_the_source_message_is_reacted_when_the_order_arrives(wired):
    services = wired
    await _configure_source_stages(received="👀")

    payload = text_payload(SOURCE_CHAT_ID, "New Order")
    order_id = await deliver_order(services, payload)

    reaction = services.gateway.reactions[-1]
    assert (reaction.chat_id, reaction.message_id) == (SOURCE_CHAT_ID, payload.message_id)
    assert reaction.reaction == "👀"
    assert (await get_order(order_id)).source_reaction_stage == "RECEIVED"


async def test_an_operator_progress_reaction_moves_the_source_reaction(wired):
    services = wired
    await _configure_source_stages(received="👀", in_progress="⏳")
    async with session_scope() as session:
        await SourceReactionRepository(session).add_progress_reaction("👍")

    payload = text_payload(SOURCE_CHAT_ID, "New Order")
    order_id = await deliver_order(services, payload)
    _chat, message_id = await primary_work_group_message(order_id, WORK_GROUP_CHAT_ID)

    await services.source_reactions.mark_in_progress(order_id, OPERATOR_ID)

    order = await get_order(order_id)
    assert order.source_reaction_stage == "IN_PROGRESS"
    assert order.in_progress_at is not None
    assert order.in_progress_by_user_id == OPERATOR_ID
    last = services.gateway.reactions[-1]
    assert (last.chat_id, last.message_id, last.reaction) == (
        SOURCE_CHAT_ID,
        payload.message_id,
        "⏳",
    )


async def test_success_and_failure_set_their_own_source_reaction(destinations):
    services = destinations
    await _configure_source_stages(success="💯", failed="👎")
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    await configure_acknowledgement(OrderStatus.FAILED, enabled=False)

    ok_payload = text_payload(SOURCE_CHAT_ID, "good")
    ok_id = await deliver_order(services, ok_payload)
    await services.finalizer.manual_override(ok_id, OrderStatus.SUCCESS, 1000)

    bad_payload = text_payload(SOURCE_CHAT_ID, "bad")
    bad_id = await deliver_order(services, bad_payload)
    await services.finalizer.manual_override(bad_id, OrderStatus.FAILED, 1000)

    on_source = {
        (r.message_id, r.reaction)
        for r in services.gateway.reactions
        if r.chat_id == SOURCE_CHAT_ID
    }
    assert (ok_payload.message_id, "💯") in on_source
    assert (bad_payload.message_id, "👎") in on_source


async def test_a_disabled_stage_places_no_reaction(wired):
    services = wired
    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "New Order"))
    assert [r for r in services.gateway.reactions if r.chat_id == SOURCE_CHAT_ID] == []


async def test_a_stage_is_never_applied_twice(wired):
    services = wired
    await _configure_source_stages(received="👀")
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "New Order"))
    before = len(services.gateway.reactions)

    await services.source_reactions.apply(order_id, SourceReactionStage.RECEIVED)
    assert len(services.gateway.reactions) == before


async def test_a_late_earlier_stage_cannot_undo_a_later_one(destinations):
    """A delayed RECEIVED must not overwrite the SUCCESS mark."""
    services = destinations
    await _configure_source_stages(received="👀", success="💯")
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "New Order"))
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)
    assert (await get_order(order_id)).source_reaction_stage == "SUCCESS"

    outcome = await services.source_reactions.apply(order_id, SourceReactionStage.RECEIVED)
    assert outcome.applied is False
    assert (await get_order(order_id)).source_reaction_stage == "SUCCESS"


async def test_a_failed_source_reaction_never_affects_the_order(destinations):
    services = destinations
    await _configure_source_stages(success="💯")
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    services.gateway.failing_reaction_chats.add(SOURCE_CHAT_ID)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "New Order"))
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert order.source_reaction_stage is None
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1


# ===========================================================================
# WooCommerce store update
# ===========================================================================
async def _configure_store(status: OrderStatus, woo_status: str, note: str | None = None):
    from tests.fakes import FakeWooCommerceClient

    FakeWooCommerceClient.reset()
    async with session_scope() as session:
        settings = SettingRepository(session)
        await settings.set(SettingKey.WOO_BASE_URL, "https://shop.example")
        await settings.set(SettingKey.WOO_CONSUMER_KEY, "ck_test")
        await settings.set(SettingKey.WOO_CONSUMER_SECRET, "cs_test")
        await ResultConfigRepository(session).update(
            status,
            woo_enabled=True,
            woo_status=woo_status,
            woo_note_enabled=note is not None,
            woo_note=note,
        )
    return FakeWooCommerceClient


async def test_a_successful_order_updates_the_store(destinations):
    services = destinations
    client = await _configure_store(OrderStatus.SUCCESS, "completed")
    await _require_order_number()
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple\n1234567"))
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)

    assert client.calls == [
        {
            "order_number": "1234567",
            "status": "completed",
            "note": None,
            "base_url": "https://shop.example",
        }
    ]
    async with session_scope() as session:
        call = await WooCommerceRepository(session).get_call(order_id)
    assert call.status == DispatchStatus.SENT


async def test_a_failed_order_updates_the_store_differently(destinations):
    services = destinations
    client = await _configure_store(OrderStatus.FAILED, "cancelled", note="اطلاعات اشتباه بود")
    await _require_order_number()
    await configure_acknowledgement(OrderStatus.FAILED, enabled=False)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple\n7654321"))
    await services.finalizer.manual_override(order_id, OrderStatus.FAILED, 1000)

    assert client.calls[0]["order_number"] == "7654321"
    assert client.calls[0]["status"] == "cancelled"
    assert client.calls[0]["note"] == "اطلاعات اشتباه بود"


async def test_the_note_template_can_use_placeholders(destinations):
    services = destinations
    client = await _configure_store(
        OrderStatus.SUCCESS, "completed", note="تلگرام {order} · فروشگاه {number}"
    )
    await _require_order_number()
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple\n1234567"))
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)

    assert client.calls[0]["note"] == "تلگرام order1 · فروشگاه 1234567"


async def test_the_store_is_updated_exactly_once(destinations):
    services = destinations
    client = await _configure_store(OrderStatus.SUCCESS, "completed")
    await _require_order_number()
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple\n1234567"))
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)
    for _ in range(3):
        await services.finalizer.run_pipeline(order_id)

    assert len(client.calls) == 1


async def test_a_store_failure_never_changes_the_order(destinations):
    services = destinations
    client = await _configure_store(OrderStatus.SUCCESS, "completed")
    client.fail_with = "HTTP 404: order not found"
    await _require_order_number()
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple\n1234567"))
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    # The Telegram result still went out; only the store call failed.
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1
    async with session_scope() as session:
        call = await WooCommerceRepository(session).get_call(order_id)
    assert call.status == DispatchStatus.FAILED
    assert "store_update_failed" in services.notifier.kinds()


async def test_the_store_is_skipped_without_an_order_number(destinations):
    """Nothing identifies the store order, so the call is not attempted."""
    services = destinations
    client = await _configure_store(OrderStatus.SUCCESS, "completed")
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    # Order-number requirement left OFF, and this text carries none.
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "no number"))
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)

    assert client.calls == []
    async with session_scope() as session:
        assert await WooCommerceRepository(session).get_call(order_id) is None


async def test_the_store_is_skipped_when_disabled(destinations):
    from tests.fakes import FakeWooCommerceClient

    services = destinations
    FakeWooCommerceClient.reset()
    await _require_order_number()
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple\n1234567"))
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)

    assert FakeWooCommerceClient.calls == []


async def test_a_conflicted_order_updates_nothing(destinations):
    services = destinations
    client = await _configure_store(OrderStatus.SUCCESS, "completed")
    await _require_order_number()
    await _configure_source_stages(success="💯")
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",))
    await configure_rule(OrderStatus.FAILED, signals=(SignalKey.REACTION,), reactions=("✅",))

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple\n1234567"))
    _chat, message_id = await primary_work_group_message(order_id, WORK_GROUP_CHAT_ID)
    await operator_reacts(services, order_id, WORK_GROUP_CHAT_ID, message_id, "✅", OPERATOR_ID)

    assert (await get_order(order_id)).status == OrderStatus.CONFLICT
    assert client.calls == []
    assert services.gateway.messages_in(SUCCESS_CHAT_ID) == []


# --- readable store errors -------------------------------------------------
def test_store_error_is_shown_in_the_store_s_own_words():
    """A WooCommerce error must reach the admin readable, not as escapes."""
    from app.integrations.woocommerce import describe_error_body

    body = (
        '{"code":"woocommerce_rest_authentication_error",'
        '"message":"\\u06a9\\u0644\\u06cc\\u062f API \\u0627\\u0631\\u0627\\u0626\\u0647'
        ' \\u0634\\u062f\\u0647 \\u0645\\u062c\\u0648\\u0632 \\u062e\\u0648\\u0627\\u0646'
        '\\u062f\\u0646 \\u0646\\u062f\\u0627\\u0631\\u062f","data":{"status":401}}'
    )
    described = describe_error_body(body)
    assert "کلید API ارائه شده مجوز خواندن ندارد" in described
    assert "woocommerce_rest_authentication_error" in described
    assert "\\u06a9" not in described


def test_a_non_json_store_error_is_passed_through():
    from app.integrations.woocommerce import describe_error_body

    assert describe_error_body("<html>502 Bad Gateway</html>") == "<html>502 Bad Gateway</html>"
