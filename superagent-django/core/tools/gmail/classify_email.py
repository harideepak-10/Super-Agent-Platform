"""
Email classification tool — categorises emails using keyword matching.

Zone: GREEN — runs automatically, no human approval required.

No external API or LLM call is made.  Classification is entirely
rule-based using keyword matching, which is fast, deterministic,
and free.  For higher accuracy, this can be upgraded to use an LLM
in a later milestone.

Categories:
    invoice              — Bills, receipts, payment requests
    supplier_inquiry     — Quotes, RFQs, vendor messages
    customer_complaint   — Complaints, refund requests, issues
    newsletter           — Marketing emails, promotions, digests
    contract             — Agreements, NDAs, SOWs, legal docs
    payment_confirmation — Payment received / transaction success
    other                — Anything that doesn't match above
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


# ---------------------------------------------------------------------------
# Keyword taxonomy
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "invoice": [
        "invoice", "bill", "receipt", "amount due", "payment due",
        "balance due", "remittance", "due date", "please find attached",
        "total amount", "net 30", "net 60", "purchase order", "po #",
        "tax invoice", "proforma",
    ],
    "supplier_inquiry": [
        "inquiry", "enquiry", "quotation", "quote", "rfq", "request for quote",
        "supplier", "vendor", "procurement", "supply chain", "catalogue",
        "catalog", "pricing", "bulk order", "minimum order", "lead time",
    ],
    "customer_complaint": [
        "complaint", "unhappy", "dissatisfied", "disappointed", "frustrated",
        "issue", "problem", "broken", "not working", "doesn't work",
        "refund", "return", "damaged", "wrong item", "overcharged",
        "unacceptable", "terrible", "awful", "worst",
    ],
    "newsletter": [
        "newsletter", "unsubscribe", "subscribe", "marketing", "promotion",
        "promotional", "offer", "sale", "discount", "deal", "promo",
        "coupon", "you're receiving this", "opt out", "mailing list",
        "digest", "weekly roundup", "monthly update",
    ],
    "contract": [
        "contract", "agreement", "terms and conditions", "nda",
        "non-disclosure", "msa", "master service", "statement of work",
        "sow", "please sign", "execute this", "legal", "binding",
        "obligations", "indemnification", "liability clause",
    ],
    "payment_confirmation": [
        "payment received", "payment confirmed", "payment successful",
        "transaction complete", "transaction successful", "payment processed",
        "thank you for your payment", "your order has been paid",
        "payment id", "transaction id", "amount credited",
    ],
}

# Which categories typically require a reply
_REQUIRES_REPLY: dict[str, bool] = {
    "invoice": True,
    "supplier_inquiry": True,
    "customer_complaint": True,
    "newsletter": False,
    "contract": True,
    "payment_confirmation": False,
    "other": True,  # conservative default
}

# Thresholds for confidence scoring
_HIGH_CONFIDENCE_THRESHOLD = 3
_MEDIUM_CONFIDENCE_THRESHOLD = 1


class ClassifyEmailTool(BaseTool):
    """Classify an email into a business category using keyword matching.

    Input format (JSON string or plain email text)::

        {"email_content": "Please find attached invoice #1042..."}

    Or simply pass the raw email text as a string.

    Returns:
        JSON string with keys:
            ``category``      : str  — one of the seven category names
            ``confidence``    : str  — "high", "medium", or "low"
            ``summary``       : str  — one-sentence description
            ``requires_reply``: bool — whether a reply is expected
    """

    name: str = "classify_email"
    description: str = (
        "Classifies an email into a business category using keyword matching. "
        "Input: email text or JSON {\"email_content\": \"...\"}. "
        "Returns JSON with category, confidence, summary, requires_reply."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        """Classify the email content.

        Args:
            input_str: The email text or a JSON string containing
                       ``email_content``.

        Returns:
            JSON string with classification result.

        Raises:
            ValueError: If input_str is empty or None.
        """
        if not input_str or not input_str.strip():
            raise ValueError("ClassifyEmailTool received empty input.")

        email_text = self._extract_text(input_str)
        category, score = self._classify(email_text)
        confidence = self._score_to_confidence(score)
        summary = self._generate_summary(email_text, category)
        requires_reply = _REQUIRES_REPLY.get(category, True)

        return json.dumps({
            "category": category,
            "confidence": confidence,
            "summary": summary,
            "requires_reply": requires_reply,
        }, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(input_str: str) -> str:
        """Extract the email text from raw input or JSON wrapper.

        Args:
            input_str: Raw input string.

        Returns:
            Plain email text for classification.
        """
        stripped = input_str.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                return str(data.get("email_content", stripped))
            except json.JSONDecodeError:
                pass
        return stripped

    @staticmethod
    def _classify(text: str) -> tuple[str, int]:
        """Run keyword matching against all categories.

        Args:
            text: Normalised email text.

        Returns:
            Tuple of (best_category: str, match_count: int).
        """
        lower_text = text.lower()
        scores: dict[str, int] = {}

        for category, keywords in _CATEGORY_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in lower_text)
            scores[category] = count

        best_category = max(scores, key=lambda c: scores[c])
        best_score = scores[best_category]

        if best_score == 0:
            return "other", 0

        return best_category, best_score

    @staticmethod
    def _score_to_confidence(score: int) -> str:
        """Convert a raw keyword match count to a confidence label.

        Args:
            score: Number of keyword matches.

        Returns:
            "high", "medium", or "low".
        """
        if score >= _HIGH_CONFIDENCE_THRESHOLD:
            return "high"
        if score >= _MEDIUM_CONFIDENCE_THRESHOLD:
            return "medium"
        return "low"

    @staticmethod
    def _generate_summary(text: str, category: str) -> str:
        """Generate a one-sentence summary of the email.

        Extracts the first meaningful sentence from the email body.
        Falls back to a category-based generic summary if extraction fails.

        Args:
            text:     Full email text.
            category: Detected category.

        Returns:
            A short summary string (one sentence, max 120 chars).
        """
        # Strip quoted text (lines starting with >)
        clean_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith(">")
        ]
        clean_text = " ".join(clean_lines)

        # Try to extract the first sentence
        sentences = re.split(r"(?<=[.!?])\s+", clean_text.strip())
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) >= 15:  # skip very short fragments
                return sentence[:120]

        # Generic fallback
        generic = {
            "invoice": "An invoice or payment request has been received.",
            "supplier_inquiry": "A supplier or vendor inquiry has been received.",
            "customer_complaint": "A customer complaint or issue has been reported.",
            "newsletter": "A newsletter or marketing email has been received.",
            "contract": "A contract or legal agreement requires attention.",
            "payment_confirmation": "A payment confirmation has been received.",
            "other": "An email requiring review has been received.",
        }
        return generic.get(category, "An email has been received.")
