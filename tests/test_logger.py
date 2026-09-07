"""日志文件选择与多行记录保留的回归测试。"""

from __future__ import annotations

import logging
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import logger as logger_module
from utils.logger import LogManager, setup_logger


class LogManagerRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.log_file = Path(self.temp_dir.name) / "app.log"

    def test_search_and_filtered_export_keep_complete_traceback_record(self) -> None:
        original_record = (
            "2026-09-07 12:00:00 [ERROR] export: 导出失败\n"
            "Traceback (most recent call last):\n"
            '  File "worker.py", line 8, in run\n'
            "ValueError: 原始原因\n\n"
            "The above exception was the direct cause of the following exception:\n\n"
            "Traceback (most recent call last):\n"
            '  File "worker.py", line 12, in export\n'
            "RuntimeError: 唯一异常链标记\n\n"
        )
        self.log_file.write_text(
            original_record
            + "2026-09-07 12:01:00 [INFO] export: 后续日志\n",
            encoding="utf-8",
        )
        manager = LogManager(str(self.log_file))

        matches = manager.search_logs("唯一异常链标记")

        self.assertEqual(1, len(matches))
        self.assertIn("Traceback", matches[0][3])
        self.assertIn("唯一异常链标记", matches[0][3])

        exported = Path(self.temp_dir.name) / "filtered.txt"
        manager.export_logs(str(exported), level="ERROR")
        exported_text = exported.read_text(encoding="utf-8")
        self.assertIn(original_record, exported_text)
        self.assertNotIn("后续日志", exported_text)

    def test_unfiltered_export_preserves_every_byte(self) -> None:
        original = (
            b"unstructured prefix\xff\r\n"
            b"2026-09-07 12:00:00 [ERROR] export: error\r\n"
            b"Traceback\n\r\nlast line without newline"
        )
        self.log_file.write_bytes(original)
        output = self.log_file.with_suffix(".export")
        LogManager(str(self.log_file)).export_logs(str(output))
        self.assertEqual(original, output.read_bytes())

    def test_filtered_export_keeps_original_header_crlf_and_blank_lines(self) -> None:
        record = (
            b"2026-09-07 12:00:00 [ERROR] export: \r\n"
            b"Traceback\r\n\r\nRuntimeError: detail\r\n"
        )
        self.log_file.write_bytes(
            b"2026-09-07 11:00:00 [ERROR] export: earlier\n" + record
            + b"2026-09-07 12:01:00 [INFO] export: later\n"
        )
        output = self.log_file.with_suffix(".export")
        manager = LogManager(str(self.log_file))
        manager.export_logs(
            str(output), level="error", start_time="2026-09-07 12:00:00",
            end_time="2026-09-07 12:00:00",
        )
        self.assertEqual(record, output.read_bytes())
        self.assertEqual(1, len(manager.search_logs("detail")))
        self.assertEqual([], manager.search_logs("", max_results=0))

    def test_export_has_no_search_result_limit(self) -> None:
        record = b"2026-09-07 12:00:00 [ERROR] export: failure\n"
        self.log_file.write_bytes(record * 100_001)
        output = self.log_file.with_suffix(".export")
        manager = LogManager(str(self.log_file))
        manager.export_logs(str(output), level="ERROR")
        self.assertEqual(self.log_file.read_bytes(), output.read_bytes())
        self.assertEqual(2, len(manager.search_logs("failure", max_results=2)))

    def test_export_to_same_file_rejects_without_truncating_source(self) -> None:
        original = b"2026-09-07 12:00:00 [ERROR] export: failure\n"
        self.log_file.write_bytes(original)
        with self.assertRaises(ValueError):
            LogManager(str(self.log_file)).export_logs(str(self.log_file))
        self.assertEqual(original, self.log_file.read_bytes())


class LoggerDirectoryFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_handlers = logging.getLogger().handlers[:]
        self.original_level = logging.getLogger().level
        logging.getLogger().handlers = []
        self.addCleanup(self._restore_root_handlers)
        patcher = mock.patch.object(logger_module, "_global_log_manager", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        stderr = mock.patch("sys.stderr", io.StringIO())
        stderr.start()
        self.addCleanup(stderr.stop)

    def _restore_root_handlers(self) -> None:
        root = logging.getLogger()
        for handler in root.handlers[:]:
            handler.close()
            root.removeHandler(handler)
        for handler in self.original_handlers:
            root.addHandler(handler)
        root.setLevel(self.original_level)

    def test_setup_logger_skips_directory_whose_log_file_cannot_open(self) -> None:
        blocked_dir = str(Path(self.temp_dir.name) / "blocked")
        fallback_dir = str(Path(self.temp_dir.name) / "fallback")
        real_handler = logger_module.RotatingFileHandler

        def selective_handler(filename, *args, **kwargs):
            if os.path.commonpath([filename, blocked_dir]) == blocked_dir:
                raise PermissionError("blocked log file")
            return real_handler(filename, *args, **kwargs)

        with mock.patch.object(logger_module, "RotatingFileHandler", selective_handler), mock.patch.dict(
            os.environ,
            {"LOCALAPPDATA": fallback_dir},
            clear=False,
        ):
            configured = setup_logger(blocked_dir)

        file_handlers = [
            handler
            for handler in configured.handlers
            if isinstance(handler, logging.FileHandler)
        ]
        self.assertEqual(1, len(file_handlers))
        self.assertTrue(
            os.path.commonpath([file_handlers[0].baseFilename, fallback_dir])
            == fallback_dir
        )
        try:
            raise RuntimeError("fallback traceback marker")
        except RuntimeError:
            configured.exception("fallback error")
        content = Path(file_handlers[0].baseFilename).read_text(encoding="utf-8")
        self.assertIn("Traceback", content)
        self.assertIn("fallback traceback marker", content)

    def test_reconfiguration_updates_manager_to_actual_file(self) -> None:
        setup_logger(str(Path(self.temp_dir.name) / "first"))
        root = setup_logger(str(Path(self.temp_dir.name) / "second"))
        actual = next(h.baseFilename for h in root.handlers
                      if isinstance(h, logging.FileHandler))
        self.assertEqual(actual, logger_module.get_log_manager().log_file)

    def test_all_file_open_failures_leave_console_usable(self) -> None:
        with mock.patch.object(
            logger_module, "RotatingFileHandler", side_effect=PermissionError
        ):
            root = setup_logger(self.temp_dir.name)
        self.assertFalse(any(isinstance(h, logging.FileHandler) for h in root.handlers))
        self.assertTrue(root.handlers)
