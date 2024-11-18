"""
End to end tests for monitoring runs to upload with regex patterns
defined in the config file to limit what runs are uploaded

We will create 3 runs in the same directory with different samplesheets,
where the sample names in each inform what assay the sequencing is for.
We will simulate monitoring with 2 patterns to upload from the same
directory but to upload to 2 different remote paths for run_1 and run_2,
with run_3 not matching either pattern and therefore should be omitted.
"""

from argparse import Namespace
from copy import deepcopy
from datetime import datetime
from glob import glob
import json
from pathlib import Path
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


class TestConfigRegexPatternsAgainstSampleNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # clean up first in case of previous failed tests
        cleanup_local_test_files()

        # create test sequencing runs in one sequencer output directory
        cls.run_1 = os.path.join(TEST_DATA_DIR, "sequencer_a", "run_1")
        cls.run_2 = os.path.join(TEST_DATA_DIR, "sequencer_a", "run_2")
        cls.run_3 = os.path.join(TEST_DATA_DIR, "sequencer_a", "run_3")

        codes = ["assay_1", "assay_2", "assay_3"]

        with open(os.path.join(TEST_DATA_DIR, "example_samplesheet.csv")) as f:
            base_samplesheet = f.read().splitlines()

        # create as a complete runs with some example files
        for run_dir, code in zip((cls.run_1, cls.run_2, cls.run_3), codes):
            create_files(
                run_dir,
                "RunInfo.xml",
                "CopyComplete.txt",
                "Config/Options.cfg",
                "InterOp/EventMetricsOut.bin",
            )

            # example samplesheet has `assay_1` for all samples, replace
            # this with the current assay code we want to write in
            run_samplesheet = deepcopy(base_samplesheet)
            run_samplesheet = [
                x.replace("assay_1", code) for x in run_samplesheet
            ]

            with open(os.path.join(run_dir, "samplesheet.csv"), "w") as fh:
                fh.write("\n".join(run_samplesheet))

        # define separate full unique paths to upload test runs to by
        # the assay type they're for
        now = datetime.now().strftime("%y%m%d_%H%M%S")
        cls.parent_remote_path = f"s3_upload_e2e_test/{now}"
        cls.run_1_remote_path = f"{cls.parent_remote_path}/assay_1"
        cls.run_2_remote_path = f"{cls.parent_remote_path}/assay_2"

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
                        "sample_regex": "assay_1",
                    },
                    {
                        "monitored_directories": [
                            os.path.join(TEST_DATA_DIR, "sequencer_a"),
                        ],
                        "bucket": S3_BUCKET,
                        "remote_path": cls.run_2_remote_path,
                        "sample_regex": "assay_2",
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
        with open(
            os.path.join(TEST_DATA_DIR, "logs/s3_upload.log"), "r"
        ) as fh:
            cls.upload_log = fh.read().splitlines()

    @classmethod
    def tearDownClass(cls):

        cleanup_local_test_files(cls.run_1)
        cleanup_remote_files(cls.parent_remote_path)

        cls.mock_args.stop()
        cls.mock_flock.stop()
        cls.mock_slack.stop()

    def test_remote_files_uploaded_correctly(self):
        """
        Test that run_1 and run_2 both correctly upload to the expected
        remote paths and that run_3 is not uploaded
        """
        local_files = [
            "assay_1/run_1/RunInfo.xml",
            "assay_1/run_1/CopyComplete.txt",
            "assay_1/run_1/samplesheet.csv",
            "assay_1/run_1/Config/Options.cfg",
            "assay_1/run_1/InterOp/EventMetricsOut.bin",
            "assay_2/run_2/RunInfo.xml",
            "assay_2/run_2/CopyComplete.txt",
            "assay_2/run_2/samplesheet.csv",
            "assay_2/run_2/Config/Options.cfg",
            "assay_2/run_2/InterOp/EventMetricsOut.bin",
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

    def test_no_upload_log_generated_for_run_3(self):
        """
        Test that there is no upload log for run_3 since we have not
        uploaded that run
        """
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    TEST_DATA_DIR, "logs/uploads/run_3.upload.log.json"
                )
            )
        )

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
                " runs\n\t:black_square: run_1\n\t:black_square: run_2"
            )

            self.assertEqual(
                self.mock_slack.call_args[1]["message"], expected_message
            )

    def test_no_errors_in_logs(self):
        self.assertEqual([x for x in self.upload_log if "ERROR:" in x], [])
