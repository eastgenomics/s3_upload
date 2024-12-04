"""
End to end test for an upload being interrupted and failing to upload
all files, then resuming on the next run.

We will simulate this by mocking the upload_single_file method and raising
an error for one of the files, then rerun the main function to simulate
the script running again on schedule and continuing the upload. We can
then test the state of the partial and complete uploads from the log files
written to ensure they are as expected, and the calls to various functions.
"""

from argparse import Namespace
from copy import deepcopy
from datetime import datetime
import json
import unittest
from unittest.mock import patch
import os
import shutil


from e2e import BASE_CONFIG, S3_BUCKET, TEST_DATA_DIR
from e2e.helper import (
    cleanup_local_test_files,
    cleanup_remote_files,
    create_files,
    read_upload_log,
    read_stdout_stderr_log,
)
from s3_upload.s3_upload import main as s3_upload_main
from s3_upload.utils.upload import upload_single_file


class TestInterruptAndResume(unittest.TestCase):
    @staticmethod
    def upload_side_effect(**kwargs):
        """
        Helper function to pass to the side_effect param when mocking
        the upload_single_file function.

        This allows us to simulate failing the upload of a single file
        whilst still uploading the other files, resulting in a partially
        uploaded run.

        For all files except the RunInfo.xml we will simply pass through
        the call to upload_single_file with the provided arguments.
        """
        if kwargs["local_file"].endswith("RunInfo.xml"):
            raise RuntimeError("Interrupting upload of RunInfo.xml")

        return upload_single_file(**kwargs)

    @classmethod
    def setUpClass(cls):
        # clean up first in case of previous failed tests
        cleanup_local_test_files()

        # create test sequencing run in set monitored directory
        cls.run_1 = os.path.join(TEST_DATA_DIR, "sequencer_a", "run_1")

        # create as a complete run with some example files
        create_files(
            cls.run_1,
            "RunInfo.xml",
            "CopyComplete.txt",
            "Config/Options.cfg",
            "InterOp/EventMetricsOut.bin",
        )

        shutil.copy(
            os.path.join(TEST_DATA_DIR, "example_samplesheet.csv"),
            os.path.join(cls.run_1, "samplesheet.csv"),
        )

        # define full unique path to upload test runs to
        now = datetime.now().strftime("%y%m%d_%H%M%S")
        cls.remote_path = f"s3_upload_e2e_test/{now}/sequencer_a"

        # add in the sequencer to monitor with test run
        config_file = os.path.join(TEST_DATA_DIR, "test_config.json")
        config = deepcopy(BASE_CONFIG)
        config["log_dir"] = os.path.join(TEST_DATA_DIR, "logs")
        config["monitor"].append(
            {
                "monitored_directories": [
                    os.path.join(TEST_DATA_DIR, "sequencer_a")
                ],
                "bucket": S3_BUCKET,
                "remote_path": cls.remote_path,
            }
        )

        with open(config_file, "w") as fh:
            json.dump(config, fh)

        # mock command line args that would be set pointing to the config
        cls.mock_args = patch("s3_upload.s3_upload.parse_args").start()
        cls.mock_args.return_value = Namespace(
            config=config_file,
            dry_run=False,
            mode="monitor",
        )

        # mock the file lock that stops concurrent uploads as this breaks
        # when running unittest
        cls.mock_flock = patch("s3_upload.s3_upload.acquire_lock").start()

        cls.mock_slack = patch(
            "s3_upload.s3_upload.slack.post_message"
        ).start()

        # call the main entry point to run the upload, with a side effect
        # of failing to upload the RunInfo.xml file
        patch_upload = patch("utils.upload.upload_single_file")
        cls.mock_upload = patch_upload.start()
        cls.mock_upload.side_effect = cls.upload_side_effect
        s3_upload_main()

        # read in the log files after a partial upload to test state
        cls.partial_upload_log = read_upload_log()
        cls.partial_stdout_stderr_log = read_stdout_stderr_log()

        # call the upload again the simulate running on the next schedule
        # when the upload should continue and complete, resetting the
        # upload mock to just call the upload function
        cls.mock_upload.side_effect = upload_single_file
        s3_upload_main()

        # read in the log files after a upload should have completed
        cls.complete_upload_log = read_upload_log()
        cls.complete_stdout_stderr_log = read_stdout_stderr_log()

    @classmethod
    def tearDownClass(cls):

        cleanup_local_test_files(cls.run_1)
        cleanup_remote_files(cls.remote_path)

        cls.mock_args.stop()
        cls.mock_flock.stop()
        cls.mock_slack.stop()
        cls.mock_upload.stop()

    def test_partially_uploaded_log_file_as_expected(self):
        """
        Test that the run log file after we failed to upload RunInfo.xml
        is as expected and indicates a partially uploaded run to still upload
        """
        run_dir = os.path.join(TEST_DATA_DIR, "sequencer_a/run_1")
        expected_top_level_contents = {
            "run_id": "run_1",
            "run_path": run_dir,
            "completed": False,
            "total_local_files": 5,
            "total_uploaded_files": 4,
            "total_failed_upload": 1,
        }

        expected_uploaded_files = [
            "CopyComplete.txt",
            "samplesheet.csv",
            "Config/Options.cfg",
            "InterOp/EventMetricsOut.bin",
        ]
        expected_uploaded_files = sorted(
            [os.path.join(run_dir, f) for f in expected_uploaded_files]
        )

        expected_failed_files = [os.path.join(run_dir, "RunInfo.xml")]

        with self.subTest("top level correct"):
            self.assertEqual(
                self.partial_upload_log,
                {**self.partial_upload_log, **expected_top_level_contents},
            )

        with self.subTest("uploaded_files_correct"):
            self.assertEqual(
                sorted(expected_uploaded_files),
                sorted(self.partial_upload_log["uploaded_files"]),
            )

        with self.subTest("failed upload files correct"):
            self.assertEqual(
                expected_failed_files,
                self.partial_upload_log["failed_upload_files"],
            )

    def test_complete_upload_log_file_as_expected(self):
        """
        Test that the upload log after running the upload again is as
        expected and that the upload has completed
        """
        run_dir = os.path.join(TEST_DATA_DIR, "sequencer_a/run_1")

        expected_top_level_contents = {
            "run_id": "run_1",
            "run_path": run_dir,
            "completed": True,
            "total_local_files": 5,
            "total_uploaded_files": 5,
            "total_failed_upload": 0,
            "failed_upload_files": [],
        }

        expected_uploaded_files = [
            "RunInfo.xml",
            "CopyComplete.txt",
            "samplesheet.csv",
            "Config/Options.cfg",
            "InterOp/EventMetricsOut.bin",
        ]
        expected_uploaded_files = sorted(
            [os.path.join(run_dir, f) for f in expected_uploaded_files]
        )

        with self.subTest("top level correct"):
            self.assertEqual(
                self.complete_upload_log,
                {**self.complete_upload_log, **expected_top_level_contents},
            )

        with self.subTest("uploaded_files_correct"):
            self.assertEqual(
                sorted(expected_uploaded_files),
                sorted(self.complete_upload_log["uploaded_files"]),
            )

        with self.subTest("failed upload files correct"):
            self.assertEqual(
                [],
                self.complete_upload_log["failed_upload_files"],
            )

    def test_slack_post_messages_as_expected(self):
        """
        Test that our failed and complete messages are both sent correctly
        """

        with self.subTest("correct number of messages sent"):
            self.assertEqual(self.mock_slack.call_count, 2)

        with self.subTest("failed upload correct"):
            expected_call_args = {
                "url": "https://slack_webhook_alert_channel",
                "message": (
                    ":x:  *S3 Upload*: Failed uploading 1 run\n\t\t• run_1"
                ),
            }
            self.assertEqual(
                self.mock_slack.call_args_list[0][1], expected_call_args
            )

        with self.subTest("complete upload correct"):
            expected_call_args = {
                "url": "https://slack_webhook_log_channel",
                "message": (
                    ":white_check_mark:  *S3 Upload*: Successfully uploaded 1"
                    " run\n\t\t• run_1"
                ),
            }

            self.assertEqual(
                self.mock_slack.call_args_list[1][1], expected_call_args
            )

    def test_error_message_for_failed_upload_in_partial_upload_log(self):

        expected_error_from_upload_single_file = (
            "ERROR: Error in uploading"
            f" {os.path.join(TEST_DATA_DIR, 'sequencer_a/run_1', 'RunInfo.xml')}:"
            " Interrupting upload of RunInfo.xml"
        )

        expected_error_from_multi_core_upload = (
            "ERROR: 1 files failed to upload and will be logged for retrying"
        )

        with self.subTest("single_file_upload error log message"):
            assert [
                x
                for x in self.partial_stdout_stderr_log
                if expected_error_from_upload_single_file in x
            ]

        with self.subTest("multi_core_upload error log message"):
            assert [
                x
                for x in self.partial_stdout_stderr_log
                if expected_error_from_multi_core_upload in x
            ]
