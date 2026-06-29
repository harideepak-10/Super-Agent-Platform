"""
Tests for the built-in tools: CalculatorTool, CurrentTimeTool, EchoTool.

All tests run without any LLM or network calls.
"""

from __future__ import annotations

import pytest

from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool
from core.tools.echo import EchoTool
from core.tools.base_tool import ToolZone


# ---------------------------------------------------------------------------
# CalculatorTool
# ---------------------------------------------------------------------------


class TestCalculatorTool:
    """Tests for CalculatorTool."""

    def setup_method(self) -> None:
        self.calc = CalculatorTool()

    def test_name_and_zone(self) -> None:
        assert self.calc.name == "calculator"
        assert self.calc.zone == ToolZone.GREEN

    def test_simple_addition(self) -> None:
        assert self.calc.run("1200 + 800 + 3400") == "5400"

    def test_subtraction(self) -> None:
        assert self.calc.run("1000 - 350") == "650"

    def test_multiplication(self) -> None:
        assert self.calc.run("12 * 25") == "300"

    def test_division(self) -> None:
        result = self.calc.run("100 / 4")
        assert result == "25"

    def test_power(self) -> None:
        assert self.calc.run("2 ** 10") == "1024"

    def test_modulo(self) -> None:
        assert self.calc.run("17 % 5") == "2"

    def test_complex_expression(self) -> None:
        # (10 + 5) * 4 = 60
        assert self.calc.run("(10 + 5) * 4") == "60"

    def test_float_result(self) -> None:
        result = self.calc.run("7 / 2")
        assert result == "3.5"

    def test_division_by_zero_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="[Dd]ivision by zero"):
            self.calc.run("10 / 0")

    def test_invalid_expression_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            self.calc.run("not_a_number + 5")

    def test_empty_expression_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            self.calc.run("")

    def test_whitespace_only_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            self.calc.run("   ")

    def test_injection_attempt_raises_value_error(self) -> None:
        """Ensure code injection via __import__ is blocked."""
        with pytest.raises(ValueError):
            self.calc.run("__import__('os').system('ls')")

    def test_negative_numbers(self) -> None:
        assert self.calc.run("-5 + 10") == "5"

    def test_to_schema_contains_name(self) -> None:
        schema = self.calc.to_schema()
        assert schema["name"] == "calculator"
        assert schema["zone"] == "GREEN"


# ---------------------------------------------------------------------------
# CurrentTimeTool
# ---------------------------------------------------------------------------


class TestCurrentTimeTool:
    """Tests for CurrentTimeTool."""

    def setup_method(self) -> None:
        self.time_tool = CurrentTimeTool()

    def test_name_and_zone(self) -> None:
        assert self.time_tool.name == "current_time"
        assert self.time_tool.zone == ToolZone.GREEN

    def test_returns_string(self) -> None:
        result = self.time_tool.run("")
        assert isinstance(result, str)

    def test_contains_current_date_label(self) -> None:
        result = self.time_tool.run("")
        assert "Current Date" in result

    def test_contains_current_time_label(self) -> None:
        result = self.time_tool.run("")
        assert "Current Time" in result

    def test_contains_day_of_week_label(self) -> None:
        result = self.time_tool.run("")
        assert "Day of Week" in result

    def test_contains_day_type_label(self) -> None:
        result = self.time_tool.run("")
        assert "Day Type" in result

    def test_day_type_is_weekday_or_weekend(self) -> None:
        result = self.time_tool.run("anything")
        assert "Weekday" in result or "Weekend" in result

    def test_date_format(self) -> None:
        """Date should match YYYY-MM-DD pattern."""
        import re
        result = self.time_tool.run("")
        match = re.search(r"\d{4}-\d{2}-\d{2}", result)
        assert match is not None, f"No YYYY-MM-DD date found in: {result!r}"

    def test_time_format(self) -> None:
        """Time should match HH:MM:SS pattern."""
        import re
        result = self.time_tool.run("")
        match = re.search(r"\d{2}:\d{2}:\d{2}", result)
        assert match is not None, f"No HH:MM:SS time found in: {result!r}"

    def test_ignores_input(self) -> None:
        """Tool should return same-format output regardless of input."""
        result1 = self.time_tool.run("")
        result2 = self.time_tool.run("ignored input here")
        # Both should contain the same labels even if timestamps differ slightly
        assert "Current Date" in result1
        assert "Current Date" in result2


# ---------------------------------------------------------------------------
# EchoTool
# ---------------------------------------------------------------------------


class TestEchoTool:
    """Tests for EchoTool."""

    def setup_method(self) -> None:
        self.echo = EchoTool()

    def test_name_and_zone(self) -> None:
        assert self.echo.name == "echo"
        assert self.echo.zone == ToolZone.GREEN

    def test_echoes_simple_string(self) -> None:
        assert self.echo.run("hello world") == "Echo: hello world"

    def test_echoes_empty_string(self) -> None:
        result = self.echo.run("")
        assert result == "Echo: "

    def test_echoes_special_characters(self) -> None:
        input_str = "!@#$%^&*()_+"
        assert self.echo.run(input_str) == f"Echo: {input_str}"

    def test_echoes_multiline_string(self) -> None:
        input_str = "line one\nline two"
        assert self.echo.run(input_str) == f"Echo: {input_str}"

    def test_raises_on_none_input(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            self.echo.run(None)  # type: ignore[arg-type]

    def test_echoes_numbers_as_string(self) -> None:
        assert self.echo.run("12345") == "Echo: 12345"

    def test_to_schema(self) -> None:
        schema = self.echo.to_schema()
        assert schema["name"] == "echo"
        assert schema["zone"] == "GREEN"
        assert "description" in schema
