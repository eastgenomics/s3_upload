"""Functions for handling uploading into S3"""

from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from os import environ, path
import re
import sys
from typing import Dict, List, Tuple

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore import exceptions as s3_exceptions

from .log import get_logger
from .slack import post_message as post_slack_message

AWS_DEFAULT_PROFILE = environ.get("AWS_DEFAULT_PROFILE")
AWS_SECRET_KEY = environ.get("AWS_SECRET_KEY")
AWS_ACCESS_KEY = environ.get("AWS_ACCESS_KEY")

log = get_logger("s3_upload")


def check_aws_access(slack_alert_webhook=None):
    """
    Check authentication with AWS S3 with stored credentials by checking
    access to all buckets

    slack_alert_webhook : str
        webhook URL for sending alerts to

    Returns
    -------
    list
        list of available S3 buckets

    Raises
    ------
    SystemExit
        Raised when mutually exclusive AWS environment variables provided
    SystemExit
        Raised when required environment variables not defined
    RuntimeError
        Raised when unable to connect to AWS
    """
    log.info("Checking access to AWS")

    if not slack_alert_webhook:
        # try get from env if not set, mostly for testing
        slack_alert_webhook = environ.get("SLACK_ALERT_WEBHOOK")

    error_message = None
    slack_base_error = (
        ":warning:  *S3 Upload*: Error in connecting to AWS!\n\n"
    )

    if AWS_DEFAULT_PROFILE and (AWS_ACCESS_KEY or AWS_SECRET_KEY):
        error_message = (
            "Both `AWS_DEFAULT_PROFILE` provided as well as `AWS_ACCESS_KEY`"
            " and / or `AWS_SECRET_KEY`. Only one authentication method may be"
            " used."
        )

    if AWS_DEFAULT_PROFILE:
        log.info(
            "Environment variable AWS_DEFAULT_PROFILE defined, will be used"
            " for authentication"
        )
    elif AWS_ACCESS_KEY and AWS_SECRET_KEY:
        log.info(
            "Environment variables AWS_ACCESS_KEY and AWS_SECRET_KEY defined,"
            " will be used for authentication"
        )
    else:
        error_message = (
            "Required environment variables for AWS authentication not"
            " defined. Requires either `AWS_DEFAULT_PROFILE` or"
            " `AWS_ACCESS_KEY` and `AWS_SECRET_KEY`."
        )

    if error_message:
        if slack_alert_webhook:
            post_slack_message(
                url=slack_alert_webhook,
                message=slack_base_error + error_message,
            )
        log.error(error_message)
        sys.exit(error_message)

    try:
        return list(
            boto3.Session(
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY,
                profile_name=AWS_DEFAULT_PROFILE,
            )
            .resource("s3")
            .buckets.all()
        )
    except Exception as err:
        if slack_alert_webhook:
            post_slack_message(
                url=slack_alert_webhook,
                message=f"{slack_base_error}\t\t{err}",
            )

        raise RuntimeError(f"Error in connecting to AWS: {err}") from err


def check_buckets_exist(buckets, slack_alert_webhook=None) -> List[dict]:
    """
    Check that the provided bucket(s) exist and are accessible

    Parameters
    ----------
    buckets : list
        S3 bucket(s) to check access for
    slack_alert_webhook : str
        webhook URL for sending alerts to

    Returns
    -------
    list
        lists of dicts with bucket metadata

    Raises
    ------
    RuntimeError
        Raised when one or more buckets do not exist / not accessible
    """
    log.info("Checking bucket(s) exist and accessible: %s", ", ".join(buckets))

    if not slack_alert_webhook:
        # try get from env if not set, mostly for testing
        slack_alert_webhook = environ.get("SLACK_ALERT_WEBHOOK")

    valid = []
    invalid = []

    for bucket in buckets:
        try:
            log.debug("Checking %s exists and accessible", bucket)
            valid.append(
                boto3.Session(
                    aws_access_key_id=AWS_ACCESS_KEY,
                    aws_secret_access_key=AWS_SECRET_KEY,
                    profile_name=AWS_DEFAULT_PROFILE,
                )
                .client("s3")
                .head_bucket(Bucket=bucket)
            )
        except s3_exceptions.ClientError:
            invalid.append(bucket)

    if invalid:
        error_message = (
            f"{len(invalid) } bucket(s) not accessible or do not exist: "
            f"{', '.join(invalid)}"
        )

        if slack_alert_webhook:
            slack_alert_message = (
                ":warning:  *S3 Upload*: Error in accessing specified S3"
                f" buckets!\n\n\t\t{error_message}"
            )

            post_slack_message(
                url=slack_alert_webhook, message=slack_alert_message
            )

        log.error(error_message)

        raise RuntimeError(error_message)

    log.debug("All buckets exist and accessible")
    return valid


def upload_single_file(
    s3_client, bucket, remote_path, local_file, parent_path
):
    """
    Uploads single file into S3 storage bucket

    Parameters
    ----------
    client : bootcore.client.S3
        boto3 S3 client
    bucket : str
        S3 bucket to upload to
    remote_path : str
        parent directory in bucket to upload to
    local_file : str
        file and path to upload
    parent_path : str
        path to parent of sequencing directory, will be removed from
        the file path for uploading to not form part of the remote path

    Returns
    -------
    str
        path and filename of uploaded file
    str
        ETag attribute of the uploaded file
    """
    # remove base directory and join to specified S3 location
    upload_file = re.sub(rf"^{parent_path}", "", local_file).lstrip("/")
    upload_file = path.join(remote_path, upload_file).lstrip("/")

    log.debug("Uploading %s to %s:%s", local_file, bucket, upload_file)

    # set threshold for splitting across cores to 1GB and to not use
    # multiple threads for single file upload to allow us to control
    # this better from the config
    config = TransferConfig(multipart_threshold=1024**3, use_threads=False)
    s3_client.upload_file(
        Filename=local_file,
        Bucket=bucket,
        Key=upload_file,
        Config=config,
    )

    # ensure we can access the remote file to log the object ID
    remote_object = s3_client.get_object(Bucket=bucket, Key=upload_file)

    log.debug("%s uploaded as %s", local_file, remote_object.get("ETag"))

    return local_file, remote_object.get("ETag", "").strip('"')


def _submit_to_pool(pool, func, item_input, items, **kwargs) -> dict:
    """
    Submits one call to `func` in `pool` (either ThreadPoolExecutor or
    ProcessPoolExecutor) for each item in `items`. All additional
    arguments defined in `kwargs` are passed to the given function.

    This has been abstracted from both multi_thread_upload and
    multi_core_upload to allow for unit testing of the called function
    raising exceptions that are caught and handled.

    In this context we will be calling upload_single_file() once for
    each of files in the given list of files, passing through the S3
    bucket and paths for uploading.

    Parameters
    ----------
    pool : ThreadPoolExecutor | ProcessPoolExecutor
        concurrent.futures executor to submit calls to
    func : callable
        function to call on submitting
    item_input : str
        function input field to submit each items of `items` to
    items : iterable
        iterable of object to submit

    Returns
    -------
    dict
        mapping of concurrent.futures.Future objects to the original
        `item` submitted for that future
    """
    return {
        pool.submit(
            func,
            **{**{item_input: item}, **kwargs},
        ): item
        for item in items
    }


def multi_thread_upload(
    files, bucket, remote_path, threads, parent_path
) -> Tuple[Dict[str, str], list]:
    """
    Uploads the given set of `files` to S3 on a single CPU core using
    maximum of n threads.

    We are defining one S3 client per core due to boto3 clients being thread
    safe but not safe to share across processes due to expected response
    ordering: https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html#caveat

    We will also set `disable_request_compression` since the majority of run
    data will not compress and this reduces unneeded CPU and memory load. In
    addition, `tcp_keepalive` is set to ensure the session is kept alive.
    Other available config parameters for the botocore Config object are here:
    https://botocore.amazonaws.com/v1/documentation/api/latest/reference/config.html

    Parameters
    ----------
    files : list
        list of local files to upload
    bucket : str
        S3 bucket to upload to
    remote_path : str
        parent directory in bucket to upload to
    threads : int
        maximum number of threaded process to open per core
    parent_path : str
        path to parent of sequencing directory, will be removed from
        the file path for uploading to not form part of the remote path

    Returns
    -------
    dict
        mapping of local file to ETag ID of uploaded file
    list
        list of any files that failed to upload
    """
    log.info("Uploading %s files with %s threads", len(files), threads)

    session = boto3.session.Session(
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        profile_name=AWS_DEFAULT_PROFILE,
    )
    s3_client = session.client(
        "s3",
        config=Config(
            retries={"total_max_attempts": 10, "mode": "standard"},
            disable_request_compression=True,
            tcp_keepalive=True,
            max_pool_connections=100,
        ),
    )

    uploaded_files = {}
    failed_upload = []

    with ThreadPoolExecutor(max_workers=threads) as executor:
        concurrent_jobs = _submit_to_pool(
            pool=executor,
            func=upload_single_file,
            item_input="local_file",
            items=files,
            s3_client=s3_client,
            bucket=bucket,
            remote_path=remote_path,
            parent_path=parent_path,
        )

        for future in as_completed(concurrent_jobs):
            # access returned output as each is returned in any order
            try:
                local_file, remote_id = future.result()
                uploaded_files[local_file] = remote_id
            except Exception as exc:
                # catch any errors that may get raised from uploading, we
                # will return a list of failed files to try reupload later
                log.error(
                    "Error in uploading %s: %s", concurrent_jobs[future], exc
                )
                failed_upload.append(concurrent_jobs[future])

    return uploaded_files, failed_upload


def multi_core_upload(
    files, bucket, remote_path, cores, threads, parent_path
) -> Tuple[Dict[str, str], list]:
    """
    Call the multi_thread_upload on `files` split across n
    logical CPU cores

    Parameters
    ----------
    files : list
        list of local files to upload
    bucket : str
        S3 bucket to upload to
    remote_path : str
        parent directory in bucket to upload to
    cores : int
        maximum number of logical CPU cores to split uploading across
    threads : int
        maximum number of threaded process to open per core
    parent_path : str
        path to parent of sequencing directory, will be removed from
        the file path for uploading to not form part of the remote path

    Returns
    -------
    dict
        mapping of local file to ETag ID of uploaded file
    list
        list of any files that failed to upload
    """
    log.info(
        "Beginning uploading %s files with %s cores to %s:%s",
        sum([len(x) for x in files]),
        cores,
        bucket,
        remote_path,
    )

    all_uploaded_files = {}
    all_failed_upload = []

    with ProcessPoolExecutor(max_workers=cores) as executor:
        concurrent_jobs = _submit_to_pool(
            pool=executor,
            func=multi_thread_upload,
            item_input="files",
            items=files,
            bucket=bucket,
            remote_path=remote_path,
            parent_path=parent_path,
            threads=threads,
        )

        for future in as_completed(concurrent_jobs):
            # access returned output as each is returned in any order
            try:
                uploaded_files, failed_upload = future.result()

                all_uploaded_files = {**all_uploaded_files, **uploaded_files}
                all_failed_upload.extend(failed_upload)
            except Exception as exc:
                # catch any other errors that might get raised from the
                # ProcessPool but not handled in the child ThreadPool
                all_failed_upload.extend(concurrent_jobs[future])
                log.error(
                    "Failed uploading %s files for a single ProcessPool due to"
                    " an unhandled error: %s",
                    len(concurrent_jobs[future]),
                    exc,
                )

    pool_executor = ProcessPoolExecutor(max_workers=cores)
    concurrent_jobs = _submit_to_pool(
        pool=pool_executor,
        func=multi_thread_upload,
        item_input="files",
        items=files,
        bucket=bucket,
        remote_path=remote_path,
        parent_path=parent_path,
        threads=threads,
    )

    for future in as_completed(concurrent_jobs):
        # access returned output as each is returned in any order
        try:
            uploaded_files, failed_upload = future.result()
            print("resulted")

            all_uploaded_files = {**all_uploaded_files, **uploaded_files}
            all_failed_upload.extend(failed_upload)
        except Exception as exc:
            # catch any other errors that might get raised from the
            # ProcessPool but not handled in the child ThreadPool
            all_failed_upload.extend(concurrent_jobs[future])
            print("foo bar")
            log.error(
                "Failed uploading %s files for a single ProcessPool due to"
                " an unhandled error: %s",
                len(concurrent_jobs[future]),
                exc,
            )

    pool_executor.shutdown(wait=True)

    log.info(
        "Successfully uploaded %s files to %s:%s",
        len(all_uploaded_files.keys()),
        bucket,
        remote_path,
    )
    if all_failed_upload:
        log.error(
            "%s files failed to upload and will be logged for retrying",
            len(all_failed_upload),
        )

    return all_uploaded_files, all_failed_upload
