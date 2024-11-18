"""Functions for posting Slack notifications"""

import json
import requests

from .log import get_logger


log = get_logger("s3_upload")


def format_message(completed=None, failed=None) -> str:
    """
    Format Slack message to send to on completing upload

    Parameters
    ----------
    completed : list
        list of run IDs of successfully uploaded runs
    failed : list
        list of run IDs of runs that failed uploading

    Returns
    -------
    str
        formatted message for posting to Slack
    """
    message = ""

    if completed:
        message += ":white_check_mark:  *S3 Upload*: Successfully uploaded "
        message += f"{len(completed)} runs\n\t:black_square: "
        message += "\n\t:black_square: ".join(completed)

    if failed:
        if message:
            message += "\n\n"

        message += (
            ":x:  *S3 Upload*: Failed uploading"
            f" {len(failed)} runs\n\t:black_square: "
        )
        message += "\n\t:black_square: ".join(failed)

    return message


def post_message(url, message) -> None:
    """
    Post message to provided webhook URL, used for posting messages to
    specific Slack channel

    Parameters
    ----------
    url : str
        endpoint to post message to
    message : str
        message to post to Slack
    """
    log.info("Posting message to Slack")
    try:
        response = requests.post(
            url=url,
            data=json.dumps({"text": message}),
            headers={"content-type": "application/json"},
            timeout=30,
        )

        if not response.status_code == 200:
            log.error(
                "Error in post request to Slack (%s): %s",
                response.status_code,
                response.text,
            )
    except requests.exceptions.RequestException as error:
        log.error("Error in post request to Slack: %s", error)
