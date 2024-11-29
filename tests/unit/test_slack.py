from requests.exceptions import RequestException
import unittest
from unittest.mock import patch

from s3_upload.utils import slack


class TestFormatCompleteMessage(unittest.TestCase):
    def test_completed_upload_message_correct(self):
        compiled_message = slack.format_message(completed=["run_1", "run_2"])

        expected_message = (
            ":white_check_mark:  *S3 Upload*: Successfully uploaded 2"
            " runs\n\t\t• run_1\n\t\t• run_2"
        )

        self.assertEqual(compiled_message, expected_message)

    def test_failed_upload_runs_message_correct(self):
        compiled_message = slack.format_message(failed=["run_3", "run_4"])

        expected_message = (
            ":x:  *S3 Upload*: Failed uploading 2 runs\n\t\t• run_3\n\t\t•"
            " run_4"
        )

        self.assertEqual(compiled_message, expected_message)

    def test_completed_and_failed_runs_in_same_message(self):
        compiled_message = slack.format_message(
            completed=["run_1", "run_2"], failed=["run_3", "run_4"]
        )

        expected_message = (
            ":white_check_mark:  *S3 Upload*: Successfully uploaded 2"
            " runs\n\t\t• run_1\n\t\t• run_2\n\n"
            ":x:  *S3 Upload*: Failed uploading 2 runs"
            "\n\t\t• run_3\n\t\t• run_4"
        )

        self.assertEqual(compiled_message, expected_message)


@patch("s3_upload.utils.slack.requests.post")
class TestPostMessage(unittest.TestCase):
    def test_params_set_to_requests_post(self, mock_post):
        slack.post_message(
            url="https://hooks.slack.com/services/00001",
            message="test message",
        )

        expected_call_args = {
            "url": "https://hooks.slack.com/services/00001",
            "data": '{"text": "test message"}',
            "headers": {"content-type": "application/json"},
            "timeout": 30,
        }

        self.assertDictEqual(dict(mock_post.call_args[1]), expected_call_args)

    def test_error_logged_when_non_200_response_received(self, mock_post):
        mock_post.return_value.status_code = 400
        mock_post.return_value.text = "message failed to send :sadpanda:"

        with self.assertLogs("s3_upload", level="DEBUG") as log:
            slack.post_message(
                url="https://hooks.slack.com/services/00001",
                message="test message",
            )

            expected_log_error = (
                "Error in post request to Slack (400): message failed to send"
                " :sadpanda:"
            )

            self.assertIn(expected_log_error, "".join(log.output))

    def test_error_logged_when_posting_raises_a_request_exception(
        self, mock_post
    ):
        mock_post.side_effect = RequestException("failed to post")

        with self.assertLogs("s3_upload", level="DEBUG") as log:
            slack.post_message(
                url="https://hooks.slack.com/services/00001",
                message="test message",
            )

            expected_log_error = (
                "Error in post request to Slack: failed to post"
            )

            self.assertIn(expected_log_error, "".join(log.output))
