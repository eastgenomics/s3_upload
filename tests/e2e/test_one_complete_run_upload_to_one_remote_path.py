"""
End to end tests for running the upload in monitor mode.

The below tests will fully simulate the upload process of a single
sequencing run by setting up test run structure, calling the main entry
point and testing the upload behaviour. We will upload all files except
the ThumbnailImage PNGs, which will be specified to exclude in the config.

This is the minimal end to end upload scenario to test.
"""

from argparse import Namespace
from copy import deepcopy
from datetime import datetime
import json
import unittest
from unittest.mock import patch
import os
import shutil

from e2e import BASE_CONFIG, S3_BUCKET, S3_SESSION, TEST_DATA_DIR
from e2e.helper import (
    cleanup_local_test_files,
    cleanup_remote_files,
    create_files,
)

from s3_upload.s3_upload import main as s3_upload_main


class TestSingleCompleteRun(unittest.TestCase):
    """
    End to end tests for a single complete run in a monitored directory
    being correctly uploaded into the specified bucket and remote path.
    """

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
            "Recipe/HLJC5DRX5.xml"
            "Thumbnail_Images/L001/C1.1/s_1_2103_green.png",
            "Thumbnail_Images/L001/C1.1/s_1_2103_red.png",
        )

        shutil.copy(
            os.path.join(TEST_DATA_DIR, "example_samplesheet.csv"),
            os.path.join(cls.run_1, "samplesheet.csv"),
        )

        # define full unique path to upload test runs to
        now = datetime.now().strftime("%y%m%d_%H%M%S")
        cls.remote_path = f"s3_upload_e2e_test/{now}/sequencer_a"

        # add in the sequencer to monitor with test run, excluding all
        # files in the Recipe dir and all Thumbnail PNGs
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
                "exclude_patterns": ["Recipe/", ".*png"],
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

        # call the main entry point to run the upload
        s3_upload_main()

        # capture the stdout/stderr logs written to log file for testing
        with open(
            os.path.join(TEST_DATA_DIR, "logs/s3_upload.log"), "r"
        ) as fh:
            cls.upload_log = fh.read().splitlines()

    @classmethod
    def tearDownClass(cls):

        cleanup_local_test_files(cls.run_1)
        cleanup_remote_files(cls.remote_path)

        cls.mock_args.stop()
        cls.mock_flock.stop()
        cls.mock_slack.stop()

    def test_remote_files_upload_correctly(self):
        """
        Test that the remote files are uploaded with the same directory
        structure as local and to the specified bucket:/path from the
        config file, and that we correctly exclude the specified files
        from uploading as expected
        """
        local_files = [
            "RunInfo.xml",
            "CopyComplete.txt",
            "samplesheet.csv",
            "Config/Options.cfg",
            "InterOp/EventMetricsOut.bin",
        ]
        expected_remote_files = sorted(
            [os.path.join(self.remote_path, "run_1", f) for f in local_files]
        )

        bucket = S3_SESSION.resource("s3").Bucket(S3_BUCKET)
        uploaded_objects = bucket.objects.filter(Prefix=self.remote_path)
        uploaded_files = sorted([x.key for x in uploaded_objects])

        self.assertEqual(uploaded_files, expected_remote_files)

    def test_upload_log_file_for_fully_uploaded_run_correct(self):
        """
        Test that the log file that details what has been uploaded and
        marks a run as complete is correct.

        The uploaded files contains ETag IDs return from S3, so we will
        check separately just for the logged uploaded files
        """
        expected_top_level_log_contents = {
            "run_id": "run_1",
            "run_path": "/home/jethro/Projects/s3_upload/tests/e2e/test_data/sequencer_a/run_1",
            "completed": True,
            "total_local_files": 5,
            "total_uploaded_files": 5,
            "total_failed_upload": 0,
            "failed_upload_files": [],
        }
        expected_uploaded_files = [
            "/home/jethro/Projects/s3_upload/tests/e2e/test_data/sequencer_a/run_1/CopyComplete.txt",
            "/home/jethro/Projects/s3_upload/tests/e2e/test_data/sequencer_a/run_1/RunInfo.xml",
            "/home/jethro/Projects/s3_upload/tests/e2e/test_data/sequencer_a/run_1/samplesheet.csv",
            "/home/jethro/Projects/s3_upload/tests/e2e/test_data/sequencer_a/run_1/Config/Options.cfg",
            "/home/jethro/Projects/s3_upload/tests/e2e/test_data/sequencer_a/run_1/InterOp/EventMetricsOut.bin",
        ]

        upload_log = os.path.join(
            TEST_DATA_DIR, "logs/uploads/run_1.upload.log.json"
        )

        with open(upload_log, "r") as fh:
            log_contents = json.load(fh)

        with self.subTest("correct top level of log"):
            self.assertEqual(
                log_contents,
                {**log_contents, **expected_top_level_log_contents},
            )

        with self.subTest("correct local files uploaded in log"):
            uploaded_files = sorted(log_contents["uploaded_files"].keys())

            self.assertEqual(sorted(expected_uploaded_files), uploaded_files)

    @patch("s3_upload.s3_upload.sys.exit")
    def test_uploaded_run_not_picked_up_to_upload_again(self, mock_exit):
        """
        Test that when monitor runs again that the uploaded run does not
        get triggered to upload again
        """
        with self.assertLogs("s3_upload") as log:
            s3_upload_main()

            self.assertIn(
                "No sequencing runs requiring upload found. Exiting now.",
                "".join(log.output),
            )

    @patch("s3_upload.s3_upload.sys.exit")
    def test_when_nothing_to_upload_that_exit_code_is_zero(self, mock_exit):
        """
        Test that when there is nothing to upload that we cleanly exit
        """
        s3_upload_main()

        with self.subTest("exit called"):
            self.assertEqual(mock_exit.call_count, 1)

        with self.subTest("exit code"):
            self.assertEqual(mock_exit.call_args[0][0], 0)

    def test_slack_post_message_after_uploading_as_expected(self):
        with self.subTest("only one Slack message sent"):
            self.assertEqual(self.mock_slack.call_count, 1)

        with self.subTest("correct webhook used"):
            self.assertEqual(
                self.mock_slack.call_args[1]["url"],
                BASE_CONFIG["slack_log_webhook"],
            )

        with self.subTest("message formatted as expected"):
            expected_message = (
                ":white_check_mark:  *S3 Upload*: Successfully uploaded 1"
                " runs\n\t:black_square: run_1"
            )

            self.assertEqual(
                self.mock_slack.call_args[1]["message"], expected_message
            )

    def test_no_errors_in_logs(self):
        self.assertEqual([x for x in self.upload_log if "ERROR:" in x], [])
