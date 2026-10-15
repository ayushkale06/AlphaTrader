"""Cryptographic HMAC-SHA256 RiskApprovalToken generator and verifier.

Guarantees:
1. Cryptographically signs (order_request_id, symbol, side, quantity, limit_price, expires_at).
2. Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks.
3. Enforces an explicit expiration window (default: 60 seconds).
4. Strictly deterministic, non-LLM, sub-millisecond execution.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from src.common.schemas.orders import Side


@dataclass(frozen=True)
class RiskApprovalTokenPayload:
    """Structured payload embedded in a signed RiskApprovalToken."""

    order_request_id: str
    symbol: str
    side: str
    quantity: str
    limit_price: str | None
    expires_at: str  # ISO-8601 UTC timestamp


class RiskTokenManager:
    """Issues and verifies tamper-evident HMAC-SHA256 risk approval tokens."""

    def __init__(self, secret_key: str | bytes, default_ttl_seconds: int = 60) -> None:
        if isinstance(secret_key, str):
            self._secret = secret_key.encode("utf-8")
        else:
            self._secret = bytes(secret_key)
        if len(self._secret) < 16:
            msg = "RiskTokenManager secret_key must be at least 16 bytes for security."
            raise ValueError(msg)
        self.default_ttl_seconds = default_ttl_seconds

    def issue_token(
        self,
        order_request_id: UUID | str,
        symbol: str,
        side: Side | str,
        quantity: Decimal | int | str,
        limit_price: Decimal | float | str | None,
        expires_at: datetime | None = None,
    ) -> str:
        """Create a signed, base64-encoded token for an approved order proposal."""
        if expires_at is None:
            now = datetime.now(UTC)
            exp_ts = now.timestamp() + self.default_ttl_seconds
            expires_at = datetime.fromtimestamp(exp_ts, tz=UTC)
        elif expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        side_str = side.value if isinstance(side, Side) else str(side)
        qty_str = str(quantity)
        price_str = str(limit_price) if limit_price is not None else None

        payload_dict = {
            "order_request_id": str(order_request_id),
            "symbol": str(symbol),
            "side": side_str,
            "quantity": qty_str,
            "limit_price": price_str,
            "expires_at": expires_at.isoformat(),
        }

        # Canonical deterministic JSON string
        payload_bytes = json.dumps(payload_dict, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        signature = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()

        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii")
        sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii")

        return f"{payload_b64}.{sig_b64}"

    def verify_token(
        self,
        token: str,
        expected_order_request_id: UUID | str | None = None,
        expected_symbol: str | None = None,
        expected_side: Side | str | None = None,
        expected_quantity: Decimal | str | None = None,
        check_expiration: bool = True,
        current_time: datetime | None = None,
    ) -> RiskApprovalTokenPayload:
        """Verify token authenticity, expiration, and proposal binding.

        Raises:
            ValueError: If the token format is invalid, signature is forged, or expired.
        """
        parts = token.strip().split(".")
        if len(parts) != 2:
            msg = "Malformed risk approval token format. Expected '<payload>.<signature>'."
            raise ValueError(msg)

        payload_b64, sig_b64 = parts[0], parts[1]

        try:
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            expected_sig = base64.urlsafe_b64decode(sig_b64)
        except Exception as e:
            msg = f"Base64 decoding failed for risk approval token: {e}"
            raise ValueError(msg) from e

        # 1. Constant-time signature verification
        actual_sig = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(actual_sig, expected_sig):
            msg = "Tampered or invalid signature on risk approval token."
            raise ValueError(msg)

        # 2. Parse payload
        try:
            data = json.loads(payload_bytes.decode("utf-8"))
            payload = RiskApprovalTokenPayload(
                order_request_id=data["order_request_id"],
                symbol=data["symbol"],
                side=data["side"],
                quantity=data["quantity"],
                limit_price=data.get("limit_price"),
                expires_at=data["expires_at"],
            )
        except Exception as e:
            msg = f"Invalid JSON payload inside risk approval token: {e}"
            raise ValueError(msg) from e

        # 3. Check expiration
        if check_expiration:
            now = current_time or datetime.now(UTC)
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            exp_dt = datetime.fromisoformat(payload.expires_at)
            if now > exp_dt:
                msg = (
                    f"Risk approval token has expired at {payload.expires_at} "
                    f"(current: {now.isoformat()})."
                )
                raise ValueError(msg)

        # 4. Check expected bindings if provided
        if (
            expected_order_request_id is not None
            and str(expected_order_request_id) != payload.order_request_id
        ):
            msg = (
                f"Token order_request_id mismatch: expected {expected_order_request_id}, "
                f"got {payload.order_request_id}."
            )
            raise ValueError(msg)

        if expected_symbol is not None and expected_symbol != payload.symbol:
            msg = f"Token symbol mismatch: expected {expected_symbol}, got {payload.symbol}."
            raise ValueError(msg)

        if expected_side is not None:
            side_str = (
                expected_side.value if isinstance(expected_side, Side) else str(expected_side)
            )
            if side_str != payload.side:
                msg = f"Token side mismatch: expected {side_str}, got {payload.side}."
                raise ValueError(msg)

        if expected_quantity is not None and str(expected_quantity) != payload.quantity:
            msg = f"Token quantity mismatch: expected {expected_quantity}, got {payload.quantity}."
            raise ValueError(msg)

        return payload
