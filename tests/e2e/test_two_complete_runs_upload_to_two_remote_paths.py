"""
End to end tests for uploading 2 completed sequencing runs that are
in separate monitored directories and being uploaded into separate
remote paths in the same specified bucket.

We will create locally `sequencer_a/run_1` and `sequencer_b/run_2`
which we then expect to upload to `s3_upload_e2e_test/{now}/
sequencer_a/run_1` and ``s3_upload_e2e_test/{now}/sequencer_b/run_2`
respectively.
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
    read_stdout_stderr_log,
)
from s3_upload.s3_upload import main as s3_upload_main


class TestTwoCompleteRunsInSeparateMonitorDirectories(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # clean up first in case of previous failed tests
        cleanup_local_test_files()

        # create test sequencing runs in set monitored directories
        cls.run_1 = os.path.join(TEST_DATA_DIR, "sequencer_a", "run_1")
        cls.run_2 = os.path.join(TEST_DATA_DIR, "sequencer_b", "run_2")

        # create as a complete runs with some example files
        for run in (cls.run_1, cls.run_2):
            create_files(
                run,
                "RunInfo.xml",
                "CopyComplete.txt",
                "Config/Options.cfg",
                "InterOp/EventMetricsOut.bin",
            )

            shutil.copy(
                os.path.join(TEST_DATA_DIR, "example_samplesheet.csv"),
                os.path.join(run, "samplesheet.csv"),
            )

        # define separate full unique paths to upload test runs to
        now = datetime.now().strftime("%y%m%d_%H%M%S")
        cls.parent_remote_path = f"s3_upload_e2e_test/{now}"
        cls.run_1_remote_path = f"{cls.parent_remote_path}/sequencer_a"
        cls.run_2_remote_path = f"{cls.parent_remote_path}/sequencer_b"

        config_file = os.path.join(TEST_DATA_DIR, "test_config.json")

        with open(config_file, "w") as fh:
            config = deepcopy(BASE_CONFIG)
            config["log_dir"] = os.path.join(TEST_DATA_DIR, "logs")
            config["monitor"].extend(
                [
                    {
                        "monitored_directories": [
                            os.path.join(TEST_DATA_DIR, "sequencer_a"),
                        ],
                        "bucket": S3_BUCKET,
                        "remote_path": cls.run_1_remote_path,
                    },
                    {
                        "monitored_directories": [
                            os.path.join(TEST_DATA_DIR, "sequencer_b"),
                        ],
                        "bucket": S3_BUCKET,
                        "remote_path": cls.run_2_remote_path,
                    },
                ]
            )
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
        cls.upload_log = read_stdout_stderr_log()

    @classmethod
    def tearDownClass(cls):

        cleanup_local_test_files(cls.run_1, cls.run_2)
        cleanup_remote_files(cls.parent_remote_path)

        cls.mock_args.stop()
        cls.mock_flock.stop()
        cls.mock_slack.stop()

    def test_remote_file_uploaded_correctly(self):
        """
        Test that both runs upload correctly into the correct locations
        """
        local_files = [
            "sequencer_a/run_1/RunInfo.xml",
            "sequencer_a/run_1/CopyComplete.txt",
            "sequencer_a/run_1/samplesheet.csv",
            "sequencer_a/run_1/Config/Options.cfg",
            "sequencer_a/run_1/InterOp/EventMetricsOut.bin",
            "sequencer_b/run_2/RunInfo.xml",
            "sequencer_b/run_2/CopyComplete.txt",
            "sequencer_b/run_2/samplesheet.csv",
            "sequencer_b/run_2/Config/Options.cfg",
            "sequencer_b/run_2/InterOp/EventMetricsOut.bin",
        ]

        expected_remote_files = sorted(
            [os.path.join(self.parent_remote_path, f) for f in local_files]
        )

        bucket = S3_SESSION.resource("s3").Bucket(S3_BUCKET)
        uploaded_objects = bucket.objects.filter(
            Prefix=self.parent_remote_path
        )
        uploaded_files = sorted([x.key for x in uploaded_objects])

        self.assertEqual(uploaded_files, expected_remote_files)

    def test_upload_log_files_for_fully_uploaded_run_1_correct(self):
        """
        Test the upload logs for run_1 from sequencer_a are as expected
        """
        expected_top_level_log_contents = {
            "run_id": "run_1",
            "run_path": self.run_1,
            "completed": True,
            "total_local_files": 5,
            "total_uploaded_files": 5,
            "total_failed_upload": 0,
            "failed_upload_files": [],
        }
        expected_uploaded_files = [
            "CopyComplete.txt",
            "RunInfo.xml",
            "samplesheet.csv",
            "Config/Options.cfg",
            "InterOp/EventMetricsOut.bin",
        ]
        expected_uploaded_files = [
            os.path.join(self.run_1, f) for f in expected_uploaded_files
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

    def test_upload_log_files_for_fully_uploaded_run_2_correct(self):
        """
        Test the upload logs for run_2 from sequencer_b are as expected
        """
        expected_top_level_log_contents = {
            "run_id": "run_2",
            "run_path": self.run_2,
            "completed": True,
            "total_local_files": 5,
            "total_uploaded_files": 5,
            "total_failed_upload": 0,
            "failed_upload_files": [],
        }
        expected_uploaded_files = [
            "CopyComplete.txt",
            "RunInfo.xml",
            "samplesheet.csv",
            "Config/Options.cfg",
            "InterOp/EventMetricsOut.bin",
        ]
        expected_uploaded_files = [
            os.path.join(self.run_2, f) for f in expected_uploaded_files
        ]

        upload_log = os.path.join(
            TEST_DATA_DIR, "logs/uploads/run_2.upload.log.json"
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
                ":white_check_mark:  *S3 Upload*: Successfully uploaded 2"
                " runs\n\t\t• run_1\n\t\t• run_2"
            )

            self.assertEqual(
                self.mock_slack.call_args[1]["message"], expected_message
            )

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

    def test_no_errors_in_logs(self):
        self.assertEqual([x for x in self.upload_log if "ERROR:" in x], [])
