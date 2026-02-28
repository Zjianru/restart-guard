#!/usr/bin/env python3
"""
Tests for security fixes in restart-guard.
Covers:
- Webhook body template injection prevention
- Host/port validation for URL construction
"""

import os
import sys
import unittest

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import notify  # noqa: E402
import restart  # noqa: E402
import guardian  # noqa: E402


class WebhookTemplateSecurityTests(unittest.TestCase):
    """Test webhook body template rendering security."""

    def test_render_simple_json_template(self):
        template = '{"text": "{{message}}"}'
        message = "Hello world"
        result = notify._render_webhook_body(template, message)
        self.assertIsNotNone(result)
        parsed = __import__('json').loads(result)
        self.assertEqual(parsed["text"], message)

    def test_render_escapes_quotes_in_message(self):
        template = '{"text": "{{message}}"}'
        message = 'Say "hello" to everyone'
        result = notify._render_webhook_body(template, message)
        self.assertIsNotNone(result)
        parsed = __import__('json').loads(result)
        self.assertEqual(parsed["text"], message)

    def test_render_escapes_newlines_in_message(self):
        template = '{"text": "{{message}}"}'
        message = "Line 1\nLine 2\r\nLine 3"
        result = notify._render_webhook_body(template, message)
        self.assertIsNotNone(result)
        parsed = __import__('json').loads(result)
        self.assertEqual(parsed["text"], message)

    def test_render_escapes_backslashes_in_message(self):
        template = '{"text": "{{message}}"}'
        message = "C:\\Users\\test\\path"
        result = notify._render_webhook_body(template, message)
        self.assertIsNotNone(result)
        parsed = __import__('json').loads(result)
        self.assertEqual(parsed["text"], message)

    def test_render_nested_structure(self):
        template = '{"payload": {"message": "{{message}}", "source": "restart-guard"}}'
        message = "Test message"
        result = notify._render_webhook_body(template, message)
        self.assertIsNotNone(result)
        parsed = __import__('json').loads(result)
        self.assertEqual(parsed["payload"]["message"], message)

    def test_render_array_in_template(self):
        template = '["{{message}}", "static"]'
        message = "Dynamic"
        result = notify._render_webhook_body(template, message)
        self.assertIsNotNone(result)
        parsed = __import__('json').loads(result)
        self.assertEqual(parsed[0], message)

    def test_render_rejects_multiple_placeholders(self):
        template = '{"a": "{{message}}", "b": "{{message}}"}'
        message = "test"
        result = notify._render_webhook_body(template, message)
        # Multiple placeholders in non-JSON string templates are rejected
        # But JSON templates should work fine
        self.assertIsNotNone(result)

    def test_render_rejects_invalid_json_template_multiple_placeholders(self):
        # Invalid JSON with multiple placeholders should be rejected
        template = "not valid json {{message}} {{message}}"
        message = "test"
        result = notify._render_webhook_body(template, message)
        self.assertIsNone(result)

    def test_render_accepts_invalid_json_template_single_placeholder(self):
        # Invalid JSON with single placeholder falls back to string replacement
        template = "not valid json {{message}}"
        message = "test"
        result = notify._render_webhook_body(template, message)
        self.assertEqual(result, "not valid json test")

    def test_render_handles_unicode(self):
        template = '{"text": "{{message}}"}'
        message = "Hello 世界 🌍 Привет"
        result = notify._render_webhook_body(template, message)
        self.assertIsNotNone(result)
        parsed = __import__('json').loads(result)
        self.assertEqual(parsed["text"], message)


class HostPortValidationTests(unittest.TestCase):
    """Test host/port validation for URL construction."""

    def test_valid_localhost(self):
        host, port = restart.validate_host_port("127.0.0.1", "8080")
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, "8080")

    def test_valid_hostname(self):
        host, port = restart.validate_host_port("gateway.local", "18789")
        self.assertEqual(host, "gateway.local")
        self.assertEqual(port, "18789")

    def test_rejects_empty_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("", "8080")

    def test_rejects_none_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port(None, "8080")

    def test_rejects_newline_in_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("evil.com\n attacker.com", "8080")

    def test_rejects_carriage_return_in_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("evil.com\rattacker.com", "8080")

    def test_rejects_null_byte_in_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("evil.com\x00attacker.com", "8080")

    def test_rejects_space_in_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("evil.com attacker.com", "8080")

    def test_rejects_angle_brackets_in_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("evil.com<script>", "8080")

    def test_rejects_quotes_in_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port('evil.com"onload=', "8080")

    def test_rejects_pipe_in_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("evil.com|cat /etc/passwd", "8080")

    def test_rejects_backslash_in_host(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("evil.com\\attacker.com", "8080")

    def test_rejects_port_too_low(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("localhost", "0")

    def test_rejects_port_too_high(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("localhost", "65536")

    def test_rejects_negative_port(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("localhost", "-1")

    def test_rejects_non_numeric_port(self):
        with self.assertRaises(ValueError):
            restart.validate_host_port("localhost", "abc")

    def test_strips_whitespace_from_host(self):
        host, port = restart.validate_host_port("  127.0.0.1  ", "8080")
        self.assertEqual(host, "127.0.0.1")

    def test_guardian_validation_valid(self):
        host, port = guardian._validate_host_port("127.0.0.1", "18789")
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, "18789")

    def test_guardian_validation_rejects_invalid(self):
        with self.assertRaises(ValueError):
            guardian._validate_host_port("evil\nhost", "8080")

    def test_notify_validation_valid(self):
        host, port = notify._validate_host_port("127.0.0.1", "18789")
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, "18789")

    def test_notify_validation_rejects_invalid(self):
        with self.assertRaises(ValueError):
            notify._validate_host_port("evil\nhost", "8080")


if __name__ == "__main__":
    unittest.main()
