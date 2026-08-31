"""Rule set persistence (signals, text patterns, reactions)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from app.database.models import RuleReaction, RuleSignal, RuleTextPattern, StatusRule
from app.database.repositories.base import BaseRepository
from app.utils.enums import MatchMode, OrderStatus, RuleMode, SignalKey


class RuleRepository(BaseRepository):
    async def get_rule(self, status: OrderStatus) -> StatusRule:
        """Fetch the rule set for a status, creating the default if missing."""
        result = await self.session.execute(
            select(StatusRule)
            .options(
                selectinload(StatusRule.signals),
                selectinload(StatusRule.text_patterns),
                selectinload(StatusRule.reactions),
            )
            .where(StatusRule.status == status)
        )
        rule = result.scalar_one_or_none()
        if rule is not None:
            await self._ensure_signal_rows(rule)
            return rule

        await self.session.execute(
            insert(StatusRule)
            .values(status=status, enabled=True, mode=RuleMode.ANY)
            .on_conflict_do_nothing(index_elements=[StatusRule.status])
        )
        await self.session.flush()
        return await self.get_rule(status)

    async def _ensure_signal_rows(self, rule: StatusRule) -> None:
        """Guarantee one RuleSignal row per known signal key (disabled by default)."""
        existing = {signal.signal_key for signal in rule.signals}
        missing = [key for key in SignalKey if key.value not in existing]
        if not missing:
            return
        for key in missing:
            await self.session.execute(
                insert(RuleSignal)
                .values(rule_id=rule.id, signal_key=key.value, enabled=False)
                .on_conflict_do_nothing(index_elements=[RuleSignal.rule_id, RuleSignal.signal_key])
            )
        await self.session.flush()
        await self.session.refresh(rule, ["signals"])

    async def set_mode(self, status: OrderStatus, mode: RuleMode) -> StatusRule:
        rule = await self.get_rule(status)
        rule.mode = mode
        return rule

    async def set_enabled(self, status: OrderStatus, enabled: bool) -> StatusRule:
        rule = await self.get_rule(status)
        rule.enabled = enabled
        return rule

    async def set_signal_enabled(
        self, status: OrderStatus, signal_key: SignalKey, enabled: bool
    ) -> StatusRule:
        rule = await self.get_rule(status)
        for signal in rule.signals:
            if signal.signal_key == signal_key.value:
                signal.enabled = enabled
                break
        else:
            self.session.add(
                RuleSignal(rule_id=rule.id, signal_key=signal_key.value, enabled=enabled)
            )
        await self.session.flush()
        await self.session.refresh(rule, ["signals"])
        return rule

    async def toggle_signal(self, status: OrderStatus, signal_key: SignalKey) -> StatusRule:
        rule = await self.get_rule(status)
        current = next(
            (s.enabled for s in rule.signals if s.signal_key == signal_key.value), False
        )
        return await self.set_signal_enabled(status, signal_key, not current)

    async def enabled_signal_keys(self, status: OrderStatus) -> set[str]:
        rule = await self.get_rule(status)
        return {s.signal_key for s in rule.signals if s.enabled}

    # --- text patterns -------------------------------------------------
    async def add_text_pattern(
        self,
        status: OrderStatus,
        pattern: str,
        match_mode: MatchMode = MatchMode.CONTAINS,
        case_sensitive: bool = False,
    ) -> RuleTextPattern:
        rule = await self.get_rule(status)
        row = RuleTextPattern(
            rule_id=rule.id,
            pattern=pattern,
            match_mode=match_mode,
            case_sensitive=case_sensitive,
            enabled=True,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_text_patterns(self, status: OrderStatus) -> list[RuleTextPattern]:
        rule = await self.get_rule(status)
        result = await self.session.execute(
            select(RuleTextPattern)
            .where(RuleTextPattern.rule_id == rule.id)
            .order_by(RuleTextPattern.id)
        )
        return list(result.scalars())

    async def delete_text_pattern(self, pattern_id: int) -> bool:
        row = await self.session.get(RuleTextPattern, pattern_id)
        if row is None:
            return False
        await self.session.delete(row)
        return True

    async def toggle_text_pattern(self, pattern_id: int) -> RuleTextPattern | None:
        row = await self.session.get(RuleTextPattern, pattern_id)
        if row is not None:
            row.enabled = not row.enabled
        return row

    # --- reactions -----------------------------------------------------
    async def add_reaction(self, status: OrderStatus, emoji: str) -> RuleReaction:
        rule = await self.get_rule(status)
        await self.session.execute(
            insert(RuleReaction)
            .values(rule_id=rule.id, emoji=emoji, enabled=True)
            .on_conflict_do_update(
                index_elements=[RuleReaction.rule_id, RuleReaction.emoji],
                set_={"enabled": True},
            )
        )
        await self.session.flush()
        result = await self.session.execute(
            select(RuleReaction).where(
                RuleReaction.rule_id == rule.id, RuleReaction.emoji == emoji
            )
        )
        return result.scalar_one()

    async def list_reactions(self, status: OrderStatus) -> list[RuleReaction]:
        rule = await self.get_rule(status)
        result = await self.session.execute(
            select(RuleReaction)
            .where(RuleReaction.rule_id == rule.id)
            .order_by(RuleReaction.id)
        )
        return list(result.scalars())

    async def delete_reaction(self, reaction_id: int) -> bool:
        row = await self.session.get(RuleReaction, reaction_id)
        if row is None:
            return False
        await self.session.delete(row)
        return True

    async def toggle_reaction(self, reaction_id: int) -> RuleReaction | None:
        row = await self.session.get(RuleReaction, reaction_id)
        if row is not None:
            row.enabled = not row.enabled
        return row
