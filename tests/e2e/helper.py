"""
Simple helper functions used in most tests for set up and clean up
"""

from glob import glob
import json
from os import makedirs, path, remove
from pathlib import Path
import shutil

import boto3

from e2e import S3_BUCKET, TEST_DATA_DIR


def create_files(run_dir, *files):
    """
    Create the given files and intermediate paths provided from the
    given test run directory

    Parameters
    ----------
    run_dir : str
        path to test run directory

    files : list
        files and relative paths to create
    """
    for file_path in files:
        full_path = path.join(run_dir, file_path)
        parent_dir = Path(full_path).parent

        makedirs(parent_dir, exist_ok=True)
        open(full_path, encoding="utf-8", mode="a").close()


def cleanup_local_test_files(*run_dirs) -> None:
    """
    Clean up the test files and logs etc.

    To be called from TearDownClass to revert the test set up state.
    """
    for run_dir in run_dirs:
        shutil.rmtree(Path(run_dir).parent)

    for log_file in glob(path.join(TEST_DATA_DIR, "logs", "*log*")):
        remove(log_file)

    remove(path.join(TEST_DATA_DIR, "logs/uploads/run_1.upload.log.json"))
    remove(path.join(TEST_DATA_DIR, "test_config.json"))


def cleanup_remote_files(remote_path) -> None:
    """
    Clean up the uploaded test files from the remote path in the test
    S3 bucket

    Parameters
    ----------
    remote_path : str
        path where files were uploaded to
    """
    bucket = boto3.resource("s3").Bucket(S3_BUCKET)
    objects = bucket.objects.filter(Prefix=remote_path)
    objects = [{"Key": obj.key} for obj in objects]

    if objects:
        bucket.delete_objects(Delete={"Objects": objects})


def read_upload_log() -> dict:
    """
    Read in the run upload log file

    Returns
    -------
    dict
        contents of run upload log file
    """
    with open(
        path.join(TEST_DATA_DIR, "logs/uploads/run_1.upload.log.json"),
        encoding="utf8",
        mode="r",
    ) as fh:
        return json.load(fh)


def read_stdout_stderr_log() -> list:
    """
    Read the stdout / stderr log file s3_upload.log

    Returns
    -------
    list
        contents of log file
    """
    with open(
        path.join(TEST_DATA_DIR, "logs/s3_upload.log"),
        encoding="utf8",
        mode="r",
    ) as fh:
        return fh.read().splitlines()
