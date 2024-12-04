from datetime import datetime
import logging
from logging import FileHandler
import os
from pathlib import Path
from shutil import rmtree
from uuid import uuid4
import unittest
from unittest.mock import patch

import pytest

from unit import TEST_DATA_DIR
from s3_upload.utils import log


class TestGetConsoleHandler(unittest.TestCase):
    handler = log.get_console_handler()

    def test_stream_handler_returned(self):
        self.assertIsInstance(self.handler, logging.StreamHandler)

    def test_formatter_correctly_set(self):
        self.assertEqual(
            self.handler.formatter._fmt,
            "%(asctime)s [%(module)s] %(levelname)s: %(message)s",
        )


class TestSetFileHandler(unittest.TestCase):
    def setUp(self):
        self.logger = log.get_logger(
            f"s3_upload_{uuid4().hex}", log_level=logging.INFO
        )
        self.log_file = f"s3_upload.log.{datetime.now().strftime('%Y-%m-%d')}"

        log.set_file_handler(self.logger, Path(__file__).parent)
        self.logger.setLevel(5)

    def tearDown(self):
        log_file = os.path.join(Path(__file__).parent, "s3_upload.log")
        if os.path.exists(log_file):
            os.remove(log_file)

    def test_file_handler_correctly_set(self):

        with self.subTest("correct format"):
            self.assertEqual(
                self.logger.handlers[0].formatter._fmt,
                "%(asctime)s [%(module)s] %(levelname)s: %(message)s",
            )

        file_handler = [
            x for x in self.logger.handlers if isinstance(x, FileHandler)
        ]

        self.assertTrue(file_handler)

    def test_log_file_correctly_written_to(self):
        """
        The Logging object does not look to have the filename set as
        an attribute, so we can test the correct specified file is
        set by emitting a log message and reading from the file
        """
        self.logger.info("testing")

        with open(os.path.join(Path(__file__).parent, self.log_file)) as fh:
            log_contents = fh.read()

        self.assertIn("INFO: testing", log_contents)

    def test_setting_file_handler_twice_returns_the_handler(self):

        with patch(
            "s3_upload.utils.log.logging.Handler.setFormatter"
        ) as mock_file_handler:
            # test we hit the early return and don't continue through the function
            log.set_file_handler(self.logger, Path(__file__).parent)

            self.assertEqual(mock_file_handler.call_count, 0)


class TestCheckWritePermissionToLogDir(unittest.TestCase):
    def test_valid_existing_path_with_permission_does_not_raise_error(self):
        log.check_write_permission_to_log_dir(TEST_DATA_DIR)

    def test_missing_dir_with_valid_parent_dir_does_not_raise_error(self):
        test_log_dir = os.path.join(
            TEST_DATA_DIR, "sub_directory_that_does_not_exist_yet"
        )

        Path(test_log_dir).mkdir(parents=True, exist_ok=True)

        with self.subTest():
            log.check_write_permission_to_log_dir(test_log_dir)

        rmtree(test_log_dir)

    def test_dir_with_no_write_permission_raises_permission_error(self):
        test_log_dir = "/"

        expected_error = (
            f"Path to provided log directory {test_log_dir} does not appear to"
            " have write permission for current user"
        )

        with pytest.raises(PermissionError, match=expected_error):
            log.check_write_permission_to_log_dir(test_log_dir)

    def test_missing_dir_with_parent_dir_no_write_permission_raises_error(
        self,
    ):
        test_log_dir = "/sub_directory_that_does_not_exist_yet"

        expected_error = (
            "Path to provided log directory / does not appear to"
            " have write permission for current user"
        )

        with pytest.raises(PermissionError, match=expected_error):
            log.check_write_permission_to_log_dir(test_log_dir)


class TestGetLogger(unittest.TestCase):
    def test_existing_logger_returned_when_already_exists(self):
        # create new logger with a random name that won't already exist
        random_log = uuid4().hex

        logger = log.get_logger(
            logger_name=random_log,
            log_level=logging.INFO,
            log_dir=Path(__file__).parent,
        )

        # try create the same logger again, test we return early by checking
        # for calls to add the handler
        with patch(
            "s3_upload.utils.log.logging.Logger.addHandler"
        ) as mock_handle:
            logger = log.get_logger(
                logger_name=random_log,
                log_level=logging.INFO,
                log_dir=Path(__file__).parent,
            )

            self.assertEqual(mock_handle.call_count, 0)
