"""Dummy values only. No test opens or inspects the user's private .env."""

import io
import os
import unittest
from unittest.mock import patch

from fallout.local_env import ERROR, load_private_environment, parse_environment


class PrivateEnvironmentTests(unittest.TestCase):
    def test_literal_values_quotes_comments_and_crlf(self):
        self.assertEqual(parse_environment('# dummy\r\nGEMINI_API_KEY="dummy_key_12345"\r\nFALLOUT_AI_MODEL=gemini-flash-latest\nFALLOUT_AI_ENABLED=true\n'),
                         {"GEMINI_API_KEY": "dummy_key_12345", "FALLOUT_AI_MODEL": "gemini-flash-latest", "FALLOUT_AI_ENABLED": "true"})
        self.assertEqual(parse_environment("GEMINI_API_KEY='dummy_key'\nFALLOUT_AI_MODEL="),
                         {"GEMINI_API_KEY": "dummy_key", "FALLOUT_AI_MODEL": ""})

    def test_invalid_input_redacted(self):
        for text in ("UNKNOWN=dummy_secret", "GEMINI_API_KEY=dummy_secret\nGEMINI_API_KEY=other",
                     "GEMINI_API_KEY=$(dummy_secret)", "GEMINI_API_KEY=${dummy_secret}",
                     "GEMINI_API_KEY='dummy_secret", "export GEMINI_API_KEY=dummy_secret",
                     "GEMINI_API_KEY=dummy secret", "GEMINI_API_KEY=dummy;command", "x" * 16385):
            with self.subTest(text=text[:50]), self.assertRaises(ValueError) as caught:
                parse_environment(text)
            self.assertEqual(str(caught.exception), ERROR)

    def test_mock_file_overrides_shell_without_touching_real_file(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "old_dummy"}, clear=True), \
                patch("pathlib.Path.is_symlink", return_value=False), \
                patch("pathlib.Path.open", return_value=io.StringIO("GEMINI_API_KEY=new_dummy\nFALLOUT_AI_ENABLED=true")) as opened:
            load_private_environment()
            self.assertEqual(os.environ["GEMINI_API_KEY"], "new_dummy")
            self.assertEqual(os.environ["FALLOUT_AI_ENABLED"], "true")
            opened.assert_called_once_with("r", encoding="utf-8")

    def test_invalid_mock_file_does_not_partially_apply(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "old_dummy"}, clear=True), \
                patch("pathlib.Path.is_symlink", return_value=False), \
                patch("pathlib.Path.open", return_value=io.StringIO("GEMINI_API_KEY=new_dummy\nUNKNOWN=secret")):
            with self.assertRaises(ValueError):
                load_private_environment()
            self.assertEqual(dict(os.environ), {"GEMINI_API_KEY": "old_dummy"})

    def test_symlink_and_read_errors_fail_closed(self):
        with patch("pathlib.Path.is_symlink", return_value=True), patch("pathlib.Path.open") as opened:
            with self.assertRaises(ValueError):
                load_private_environment()
            opened.assert_not_called()
        with patch("pathlib.Path.is_symlink", return_value=False), patch("pathlib.Path.open", side_effect=OSError("dummy_private_detail")):
            with self.assertRaises(ValueError) as caught:
                load_private_environment()
            self.assertEqual(str(caught.exception), ERROR)

    def test_normal_launch_never_loads_private_file(self):
        import launch
        with patch("sys.argv", ["launch.py", "--help"]), patch("fallout.local_env.load_private_environment") as loader:
            with self.assertRaises(SystemExit) as caught:
                launch.main()
            self.assertEqual(caught.exception.code, 0)
            loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()