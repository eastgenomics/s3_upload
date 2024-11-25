from concurrent.futures import Future
import re
import unittest
from unittest.mock import ANY, call, Mock, patch

import boto3
from botocore import exceptions as s3_exceptions
import pytest

from s3_upload.utils import upload


@patch("s3_upload.utils.upload.post_slack_message")
@patch("s3_upload.utils.upload.boto3.Session")
class TestCheckAwsAccess(unittest.TestCase):
    @patch("s3_upload.utils.upload.AWS_ACCESS_KEY", None)
    @patch("s3_upload.utils.upload.AWS_SECRET_KEY", None)
    @patch("s3_upload.utils.upload.AWS_DEFAULT_PROFILE", "baz")
    def test_list_of_buckets_returned_on_aws_being_accessible_w_default_profile(
        self, mock_s3, mock_slack
    ):

        mock_s3.return_value.resource.return_value.buckets.all.return_value = [
            "bucket_1",
            "bucket_2",
        ]
        returned_buckets = upload.check_aws_access()

        self.assertEqual(returned_buckets, ["bucket_1", "bucket_2"])

    @patch("s3_upload.utils.upload.AWS_ACCESS_KEY", "foo")
    @patch("s3_upload.utils.upload.AWS_SECRET_KEY", "bar")
    @patch("s3_upload.utils.upload.AWS_DEFAULT_PROFILE", None)
    def test_list_of_buckets_returned_on_aws_being_accessible_w_keys(
        self, mock_s3, mock_slack
    ):

        mock_s3.return_value.resource.return_value.buckets.all.return_value = [
            "bucket_1",
            "bucket_2",
        ]
        returned_buckets = upload.check_aws_access()

        self.assertEqual(returned_buckets, ["bucket_1", "bucket_2"])

    @patch("s3_upload.utils.upload.AWS_ACCESS_KEY", None)
    @patch("s3_upload.utils.upload.AWS_SECRET_KEY", None)
    @patch("s3_upload.utils.upload.AWS_DEFAULT_PROFILE", "baz")
    def test_runtime_error_raised_on_not_being_able_to_connect(
        self, mock_s3, mock_slack
    ):
        mock_s3.side_effect = s3_exceptions.ClientError(
            {"Error": {"Code": 1, "Message": "foo"}}, "bar"
        )

        expected_error = re.escape(
            "Error in connecting to AWS: An error occurred (1) "
            "when calling the bar operation: foo"
        )

        with pytest.raises(RuntimeError, match=expected_error):
            upload.check_aws_access(slack_alert_webhook="my_webhook")

        with self.subTest("correct Slack message sent"):
            expected_slack_message = (
                ":warning:  *S3 Upload*: Error in connecting to AWS!\n\n\t\tAn"
                " error occurred (1) when calling the bar operation: foo"
            )

            self.assertEqual(
                mock_slack.call_args[1]["message"],
                expected_slack_message,
            )

    @patch("s3_upload.utils.upload.AWS_ACCESS_KEY", "foo")
    @patch("s3_upload.utils.upload.AWS_SECRET_KEY", "bar")
    @patch("s3_upload.utils.upload.AWS_DEFAULT_PROFILE", "baz")
    def test_system_exit_raised_when_all_env_variables_set(
        self, mock_s3, mock_slack
    ):
        expected_error = (
            "Both `AWS_DEFAULT_PROFILE` provided as well as `AWS_ACCESS_KEY`"
            " and / or `AWS_SECRET_KEY`. Only one authentication method may be"
            " used."
        )

        with pytest.raises(SystemExit, match=re.escape(expected_error)):
            upload.check_aws_access()

    @patch("s3_upload.utils.upload.AWS_ACCESS_KEY", None)
    @patch("s3_upload.utils.upload.AWS_SECRET_KEY", None)
    @patch("s3_upload.utils.upload.AWS_DEFAULT_PROFILE", None)
    def test_system_exit_raised_and_slack_alert_sent_when_no_env_variables_set(
        self, mock_s3, mock_slack
    ):
        expected_error = (
            "Required environment variables for AWS authentication not"
            " defined. Requires either `AWS_DEFAULT_PROFILE` or"
            " `AWS_ACCESS_KEY` and `AWS_SECRET_KEY`."
        )

        with self.subTest("SystemExit raised"), pytest.raises(
            SystemExit, match=re.escape(expected_error)
        ):
            upload.check_aws_access(slack_alert_webhook="my_webhook")

        with self.subTest("Correct Slack message sent"):
            expected_slack_message = (
                ":warning:  *S3 Upload*: Error in connecting to"
                f" AWS!\n\n{expected_error}"
            )

            self.assertEqual(
                mock_slack.call_args[1]["message"],
                expected_slack_message,
            )


@patch("s3_upload.utils.upload.post_slack_message")
@patch("s3_upload.utils.upload.boto3.Session.client")
class TestCheckBucketsExist(unittest.TestCase):
    def test_bucket_metadata_returned_when_bucket_exists(
        self, mock_client, mock_slack
    ):
        valid_bucket_metadata = {
            "ResponseMetadata": {
                "RequestId": "",
                "HostId": "host_1",
                "HTTPStatusCode": 200,
                "HTTPHeaders": {
                    "x-amz-id-2": "",
                    "x-amz-request-id": "",
                    "date": "Fri, 04 Oct 2024 13:37:34 GMT",
                    "x-amz-bucket-region": "eu-west-2",
                    "x-amz-access-point-alias": "false",
                    "content-type": "application/xml",
                    "server": "AmazonS3",
                },
                "RetryAttempts": 0,
            },
            "BucketRegion": "eu-west-2",
            "AccessPointAlias": False,
        }

        mock_client.return_value.head_bucket.return_value = (
            valid_bucket_metadata
        )

        bucket_details = upload.check_buckets_exist(["my-bucket"])

        self.assertEqual(bucket_details, [valid_bucket_metadata])

    def test_runtime_error_raised_if_one_or_more_buckets_not_valid(
        self, mock_client, mock_slack
    ):
        mock_client.side_effect = [
            s3_exceptions.ClientError(
                {"Error": {"Code": 1, "Message": "foo"}}, "bar"
            ),
            s3_exceptions.ClientError(
                {"Error": {"Code": 1, "Message": "baz"}}, "blarg"
            ),
        ]

        expected_error = (
            "2 bucket(s) not accessible or do not exist: invalid_bucket_1,"
            " invalid_bucket_2"
        )

        with self.subTest("RuntimeError raised"), pytest.raises(
            RuntimeError, match=re.escape(expected_error)
        ):
            upload.check_buckets_exist(
                buckets=["invalid_bucket_1", "invalid_bucket_2"],
                slack_alert_webhook="my_webhook",
            )

        with self.subTest("Correct Slack message sent"):
            expected_slack_message = (
                ":warning:  *S3 Upload*: Error in accessing specified S3"
                f" buckets!\n\n\t\t{expected_error}"
            )

            self.assertEqual(
                mock_slack.call_args[1]["message"],
                expected_slack_message,
            )

    def test_client_error_raised_when_bucket_does_not_exist(
        self, mock_client, mock_slack
    ):
        mock_client.side_effect = s3_exceptions.ClientError(
            {"Error": {"Code": 1, "Message": "foo"}}, "bar"
        )

        expected_error = "1 bucket(s) not accessible or do not exist: s3-test"

        with pytest.raises(RuntimeError, match=re.escape(expected_error)):
            upload.check_buckets_exist(["s3-test"])


@patch("s3_upload.utils.upload.boto3.session.Session.client")
class TestUploadSingleFile(unittest.TestCase):
    def test_upload_path_correctly_set_from_input_file_and_parent_path(
        self, mock_client
    ):
        """
        Path to upload in the bucket is set from the specified remote
        path base directory, the path of the local file and the parent
        path to remove. Test that different combinations of the above
        results in the expected upload file path.
        """

        expected_inputs_and_upload_path = [
            {
                "remote_path": "/bucket_dir1/",
                "local_file": "/path/to/monitored_dir/run1/Samplesheet.csv",
                "parent_path": "/path/to/monitored_dir/",
                "expected_upload_path": "bucket_dir1/run1/Samplesheet.csv",
            },
            {
                "remote_path": "/bucket_dir_1/bucket_dir_2",
                "local_file": "/path/to/monitored_dir/run1/Samplesheet.csv",
                "parent_path": "/path/to/monitored_dir/",
                "expected_upload_path": (
                    "bucket_dir_1/bucket_dir_2/run1/Samplesheet.csv"
                ),
            },
            {
                "remote_path": "/",
                "local_file": "/one_level_parent/run1/Samplesheet.csv",
                "parent_path": "/one_level_parent/",
                "expected_upload_path": "run1/Samplesheet.csv",
            },
        ]

        for args in expected_inputs_and_upload_path:
            with self.subTest():
                upload.upload_single_file(
                    s3_client=boto3.client(),
                    bucket="test_bucket",
                    remote_path=args["remote_path"],
                    local_file=args["local_file"],
                    parent_path=args["parent_path"],
                )

                self.assertEqual(
                    mock_client.return_value.upload_file.call_args[1]["Key"],
                    args["expected_upload_path"],
                )

    def test_local_file_name_and_object_id_returned(self, mock_client):
        mock_client.return_value.get_object.return_value = {
            "ETag": "remote_id"
        }

        local_file, remote_id = upload.upload_single_file(
            s3_client=boto3.client(),
            bucket="test_bucket",
            remote_path="/",
            local_file="/path/to/monitored_dir/run1/Samplesheet.csv",
            parent_path="/path/to/monitored_dir/",
        )

        self.assertEqual(
            (local_file, remote_id),
            (
                "/path/to/monitored_dir/run1/Samplesheet.csv",
                "remote_id",
            ),
        )


@patch("s3_upload.utils.upload.as_completed")
@patch("s3_upload.utils.upload.upload_single_file")
@patch("s3_upload.utils.upload.boto3.session.Session.client")
class TestMultiThreadUpload(unittest.TestCase):
    local_files = [
        "/path/to/monitored_dir/run1/Samplesheet.csv",
        "/path/to/monitored_dir/run1/RunInfo.xml",
        "/path/to/monitored_dir/run1/CopyComplete.txt",
    ]

    def test_correct_number_of_calls_to_upload(
        self, mock_client, mock_upload, mock_completed
    ):
        upload.multi_thread_upload(
            files=self.local_files,
            bucket="test_bucket",
            remote_path="/",
            threads=4,
            parent_path="/path/to/monitored_dir/",
        )

        self.assertEqual(mock_upload.call_count, 3)

    @patch("s3_upload.utils.upload.ThreadPoolExecutor")
    def test_correct_number_of_threads_set(
        self, mock_pool, mock_client, mock_upload, mock_completed
    ):
        for thread in [1, 4]:
            with self.subTest(f"{thread} thread(s) set to use"):
                upload.multi_thread_upload(
                    files=self.local_files,
                    bucket="test_bucket",
                    remote_path="/",
                    threads=thread,
                    parent_path="/path/to/monitored_dir/",
                )
                self.assertEqual(mock_pool.call_args[1]["max_workers"], thread)

    def test_upload_function_called_with_correct_args(
        self, mock_client, mock_upload, mock_completed
    ):
        upload.multi_thread_upload(
            files=self.local_files,
            bucket="test_bucket",
            remote_path="/",
            threads=4,
            parent_path="/path/to/monitored_dir/",
        )

        expected_call_args_for_all_calls = [
            call(
                s3_client=ANY,
                bucket="test_bucket",
                remote_path="/",
                local_file="/path/to/monitored_dir/run1/Samplesheet.csv",
                parent_path="/path/to/monitored_dir/",
            ),
            call(
                s3_client=ANY,
                bucket="test_bucket",
                remote_path="/",
                local_file="/path/to/monitored_dir/run1/RunInfo.xml",
                parent_path="/path/to/monitored_dir/",
            ),
            call(
                s3_client=ANY,
                bucket="test_bucket",
                remote_path="/",
                local_file="/path/to/monitored_dir/run1/CopyComplete.txt",
                parent_path="/path/to/monitored_dir/",
            ),
        ]

        self.assertEqual(
            expected_call_args_for_all_calls, mock_upload.call_args_list
        )

    def test_correct_file_and_id_returned(
        self, mock_client, mock_upload, mock_completed
    ):
        # each upload call per thread will return a dict containing the
        # ETag of the remote object, patch this in to the future object
        # that each thread will return
        mock_completed.return_value = [Future(), Future(), Future()]

        return_values = [
            ("/path/to/monitored_dir/run1/Samplesheet.csv", "abc"),
            ("/path/to/monitored_dir/run1/RunInfo.xml", "def"),
            ("/path/to/monitored_dir/run1/CopyComplete.txt", "ghi"),
        ]

        for i, j in zip(mock_completed.return_value, return_values):
            i.set_result(j)

        returned_uploaded_file_mapping, files_failed_upload = (
            upload.multi_thread_upload(
                files=self.local_files,
                bucket="test_bucket",
                remote_path="/",
                threads=4,
                parent_path="/path/to/monitored_dir/",
            )
        )

        expected_local_file_to_remote_id_mapping = {
            "/path/to/monitored_dir/run1/Samplesheet.csv": "abc",
            "/path/to/monitored_dir/run1/RunInfo.xml": "def",
            "/path/to/monitored_dir/run1/CopyComplete.txt": "ghi",
        }

        with self.subTest("correct file name to IDs returned"):
            self.assertEqual(
                expected_local_file_to_remote_id_mapping,
                returned_uploaded_file_mapping,
            )

        with self.subTest("no failed file uploads returned"):
            self.assertEqual(files_failed_upload, [])

    @patch("s3_upload.utils.upload.ThreadPoolExecutor")
    @patch("s3_upload.utils.upload._submit_to_pool")
    def test_list_of_failed_files_returned_on_exception_raised_from_upload(
        self, mock_submit, mock_pool, mock_client, mock_upload, mock_completed
    ):
        """
        When one or more of the calls to upload.upload_single_file fails,
        the file name that failed to upload should still be returned and
        added to a list of files that fail to upload. We will then log
        these to retry uploading on rerunning.
        """
        # mock one file uploading and 2 failing being returned as the result
        # from each future in the pool
        submitted_futures = [
            Future(),
            Future(),
            Future(),
        ]
        submitted_futures[0].set_result(
            (
                "/path/to/monitored_dir/run1/file1.txt",
                "abc",
            )
        )
        submitted_futures[1].set_exception(
            s3_exceptions.ClientError(
                {"Error": {"Code": 1, "Message": "foo"}}, "bar"
            ),
        )
        submitted_futures[2].set_exception(
            s3_exceptions.ClientError(
                {"Error": {"Code": 1, "Message": "baz"}}, "blarg"
            ),
        )

        # mock the response of all submitted futures to the pool from
        # _submit_to_pool, this returns a dict mapping the future to the
        # submitted input (i.e the file), allowing us to access the file
        # of any that the future raises an error from
        mock_submit.return_value = {
            future: input_file
            for future, input_file in zip(
                submitted_futures,
                ["file1.txt", "file2.txt", "file3.txt"],
            )
        }

        mock_completed.return_value = mock_submit.return_value

        uploaded_files, failed_files = upload.multi_thread_upload(
            files=self.local_files,
            bucket="test_bucket",
            remote_path="/",
            threads=4,
            parent_path="/path/to/monitored_dir/",
        )

        with self.subTest("successful file upload returned"):
            expected_upload = {"/path/to/monitored_dir/run1/file1.txt": "abc"}
            self.assertEqual(uploaded_files, expected_upload)

        with self.subTest("failed files returned"):
            self.assertEqual(failed_files, ["file2.txt", "file3.txt"])


class TestMultiCoreUpload(unittest.TestCase):

    local_files = [
        ["/path/to/monitored_dir/run1/Samplesheet.csv"],
        ["/path/to/monitored_dir/run1/RunInfo.xml"],
        ["/path/to/monitored_dir/run1/CopyComplete.txt"],
    ]

    @patch("s3_upload.utils.upload.as_completed")
    @patch("s3_upload.utils.upload.ProcessPoolExecutor")
    def test_correct_number_of_process_pools_set_from_cores_arg(
        self, mock_pool, mock_completed
    ):
        for core in [1, 4]:
            with self.subTest(f"{core} core(s) set to use"):
                upload.multi_core_upload(
                    files=self.local_files,
                    bucket="test_bucket",
                    remote_path="/",
                    cores=core,
                    threads=1,
                    parent_path="/path/to/monitored_dir/",
                )
                self.assertEqual(mock_pool.call_args[1]["max_workers"], core)

    @patch("s3_upload.utils.upload.as_completed")
    @patch("s3_upload.utils.upload._submit_to_pool")
    @patch("s3_upload.utils.upload.ProcessPoolExecutor")
    def test_returned_file_mapping_correct_for_all_successfully_uploading(
        self, mock_pool, mock_submit, mock_completed
    ):
        # each ProcessPool should return a dict mapping from each ThreadPool
        # of local file to remote object ID, these should then be finally
        # merged and returned as a single level dict
        submitted_futures = [Future(), Future(), Future()]

        # set futures and their response for return of as_completed()
        mock_completed.return_value = submitted_futures

        return_values = [
            ({"/path/to/monitored_dir/run1/Samplesheet.csv": "abc"}, []),
            ({"/path/to/monitored_dir/run1/RunInfo.xml": "def"}, []),
            ({"/path/to/monitored_dir/run1/CopyComplete.txt": "ghi"}, []),
        ]

        for i, j in zip(mock_completed.return_value, return_values):
            i.set_result(j)

        # set the response of the _submit_to_pool to be the dict mapping
        # the futures to input file lists
        mock_submit.return_value = {
            future: input_file
            for future, input_file in zip(
                submitted_futures,
                [
                    ["/path/to/monitored_dir/run1/Samplesheet.csv"],
                    ["/path/to/monitored_dir/run1/RunInfo.xml"],
                    ["/path/to/monitored_dir/run1/CopyComplete.txt"],
                ],
            )
        }

        uploaded_files, failed_files = upload.multi_core_upload(
            files=self.local_files,
            bucket="test_bucket",
            remote_path="/",
            cores=3,
            threads=1,
            parent_path="/path/to/monitored_dir/",
        )

        expected_local_file_to_remote_id_mapping = {
            "/path/to/monitored_dir/run1/Samplesheet.csv": "abc",
            "/path/to/monitored_dir/run1/RunInfo.xml": "def",
            "/path/to/monitored_dir/run1/CopyComplete.txt": "ghi",
        }

        with self.subTest("all files uploaded"):
            self.assertEqual(
                expected_local_file_to_remote_id_mapping,
                uploaded_files,
            )

        with self.subTest("no failed uploads"):
            self.assertEqual(failed_files, [])

    @patch("s3_upload.utils.upload.as_completed")
    @patch("s3_upload.utils.upload._submit_to_pool")
    @patch("s3_upload.utils.upload.ProcessPoolExecutor")
    def test_returned_file_mapping_correct_for_failed_upload_in_child_thread(
        self, mock_pool, mock_submit, mock_completed
    ):
        """
        Test that if an error occurs in the child ThreadPoolExecutor and
        returns a list of files that failed to upload that these are merged
        and returned
        """
        # each ProcessPool should return a dict mapping from each ThreadPool
        # of local file to remote object ID, these should then be finally
        # merged and returned as a single level dict
        submitted_futures = [Future(), Future(), Future()]

        # set futures and their response for return of as_completed(),
        # include 2 successful uploads and one fail
        mock_completed.return_value = submitted_futures

        return_values = [
            ({"/path/to/monitored_dir/run1/Samplesheet.csv": "abc"}, []),
            ({"/path/to/monitored_dir/run1/RunInfo.xml": "def"}, []),
            ({}, ["/path/to/monitored_dir/run1/CopyComplete.txt"]),
        ]

        for i, j in zip(mock_completed.return_value, return_values):
            i.set_result(j)

        # set the response of the _submit_to_pool to be the dict mapping
        # the futures to input file lists
        mock_submit.return_value = {
            future: input_file
            for future, input_file in zip(
                submitted_futures,
                [
                    ["/path/to/monitored_dir/run1/Samplesheet.csv"],
                    ["/path/to/monitored_dir/run1/RunInfo.xml"],
                    ["/path/to/monitored_dir/run1/CopyComplete.txt"],
                ],
            )
        }

        uploaded_files, failed_files = upload.multi_core_upload(
            files=self.local_files,
            bucket="test_bucket",
            remote_path="/",
            cores=3,
            threads=1,
            parent_path="/path/to/monitored_dir/",
        )

        expected_uploaded_files = {
            "/path/to/monitored_dir/run1/Samplesheet.csv": "abc",
            "/path/to/monitored_dir/run1/RunInfo.xml": "def",
        }

        expected_failed_files = [
            "/path/to/monitored_dir/run1/CopyComplete.txt"
        ]

        with self.subTest("all files uploaded"):
            self.assertEqual(
                expected_uploaded_files,
                uploaded_files,
            )

        with self.subTest("no failed uploads"):
            self.assertEqual(failed_files, expected_failed_files)

    @patch("s3_upload.utils.upload.as_completed")
    @patch("s3_upload.utils.upload._submit_to_pool")
    @patch("s3_upload.utils.upload.ProcessPoolExecutor")
    def test_exception_correctly_handled_from_parent_process(
        self, mock_pool, mock_submit, mock_completed
    ):
        """
        Test that if an error occurs in one of the main ProcessPoolExecutors
        and that we catch this to not actually exit, the error will be logged
        and failed set of upload files returned for alerting via Slack and
        retrying on next call
        """
        # set a submitted future to mock a single core upload, we will
        # raise an exception for when accessing its result object
        submitted_futures = [Mock()]
        submitted_futures[0].result.side_effect = RuntimeError(
            "unhandled test exception"
        )
        mock_completed.return_value = submitted_futures

        mock_submit.return_value = {submitted_futures[0]: self.local_files}

        with self.subTest("Error correctly caught and logged"):
            expected_error = (
                "Failed uploading 3 files for a single ProcessPool due to an"
                " unhandled error: unhandled test exception"
            )
            with self.assertLogs("s3_upload", level="ERROR") as log:
                upload.multi_core_upload(
                    files=self.local_files,
                    bucket="test_bucket",
                    remote_path="/",
                    cores=1,
                    threads=1,
                    parent_path="/path/to/monitored_dir/",
                )

                self.assertIn(expected_error, "".join(log.output))

        with self.subTest("correctly returned no upload and all failed files"):
            uploaded_files, failed_files = upload.multi_core_upload(
                files=self.local_files,
                bucket="test_bucket",
                remote_path="/",
                cores=1,
                threads=1,
                parent_path="/path/to/monitored_dir/",
            )

            self.assertEqual(
                (uploaded_files, failed_files), ({}, self.local_files)
            )
