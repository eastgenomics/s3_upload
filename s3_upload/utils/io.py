"""General IO related methods"""

import atexit
from datetime import datetime
from fcntl import flock, LOCK_EX, LOCK_NB, LOCK_UN
import json
import os
from pathlib import Path
import re
import sys
from typing import Union

from .log import get_logger

log = get_logger("s3_upload")


def acquire_lock(lock_file="/var/log/s3_upload/s3_upload.lock") -> int:
    """
    Tries to acquire an exclusive file lock on `s3_upload.lock` in log directory.

    This is to ensure only one upload process may run at once in monitor
    mode and prevent duplicate uploads where uploading takes longer than
    the schedule on which to run.

    If a process is already a lock on the file the function just cleanly
    exits with a zero exit code and will retry on the next schedule.

    Parameters
    ----------
    lock_file : str
        absolute path to file to acquire lock on

    Returns
    -------
    int
        file id of the lock file
    """
    if os.path.exists(lock_file):
        lock_fd = os.open(lock_file, flags=os.O_RDWR)
    else:
        # only set create and truncate modes if file does not already
        # exist to preserve any contents
        lock_fd = os.open(lock_file, flags=os.O_RDWR | os.O_CREAT | os.O_TRUNC)

    try:
        # LOCK_EX means that only one process can hold the lock
        # LOCK_NB means that the fcntl.flock() is not blocking
        # https://docs.python.org/3/library/fcntl.html#fcntl.flock
        flock(lock_fd, LOCK_EX | LOCK_NB)
        os.write(
            lock_fd,
            "file lock acquired from running upload at"
            f" {datetime.now().strftime('%H:%M:%S')} from process"
            f" {os.getpid()}".encode(),
        )

        # register the file lock to be released on exit
        atexit.register(release_lock, lock_fd=lock_fd)

    except BlockingIOError:
        print(
            f"Could not acquire exclusive lock on {lock_file}, assuming"
            " another upload process is currently running. Exiting now."
        )
        os.close(lock_fd)
        sys.exit(0)


def release_lock(lock_fd) -> None:
    """
    Release the file lock on the given file descriptor. To be called on
    completing the upload and closing.

    Parameters
    ----------
    lock_fd : int
        file id of the lock file
    """
    try:
        # test if provided file descriptor is valid
        os.readlink(f"/proc/self/fd/{lock_fd}")
    except FileNotFoundError:
        pass
    else:
        os.truncate(lock_fd, 0)

        flock(lock_fd, LOCK_UN)
        os.close(lock_fd)


def read_config(config) -> dict:
    """
    Read in the JSON config file

    Parameters
    ----------
    config : str
        filename of config file

    Returns
    -------
    dict
        contents of config file
    """
    log.info("Loading config from %s", config)
    with open(config, "r") as fh:
        return json.load(fh)


def read_samplesheet_from_run_directory(run_dir) -> Union[list, None]:
    """
    Finds samplesheet(s) in the given run directory and returns the
    contents as a list.

    If more than one samplesheet is found (i.e. when NovaSeqs rename
    custom named samplesheets to `SampleSheet.csv`), the contents will
    be compared to ensure they are identical and we can unambiguously
    tell we have the correct file contents.

    Parameters
    ----------
    run_dir : str
        path to run directory to search

    Returns
    -------
    list | None
        contents of samplesheet as a list if found, else None
    """
    log.info("Searching for samplesheet in run directory: %s", run_dir)

    files = os.listdir(run_dir)
    files = [
        re.search(".*sample[-_ ]?sheet.*.csv$", x, re.IGNORECASE)
        for x in files
    ]
    files = [x.group(0) for x in files if x]

    if not files:
        log.error(
            "No samplesheet found in %s to determine assay type from", run_dir
        )
        return None

    log.info("Found the following samplesheet(s): %s", ", ".join(files))

    # read all files in, split lines to lists and ensure trailing new line
    # dropped to not result in empty string in list
    all_files_contents = [
        Path(os.path.join(run_dir, x)).read_text() for x in files
    ]

    all_files_contents = [
        re.sub(r"\n$", "", x).split("\n") for x in all_files_contents
    ]

    if not all([all_files_contents[0] == x for x in all_files_contents]):
        log.error(
            "Contents of found samplesheets are not identical, can not "
            "unambiguously determine which to read contents from"
        )

        return None

    log.info("Using samplesheet %s", files[0])

    return all_files_contents[0]


def read_upload_state_log(log_file) -> dict:
    """
    Read upload state log to check if run has completed uploading

    Parameters
    ----------
    log_file : str
        path to upload state log for run

    Returns
    -------
    dict
        contents of log file
    """
    log.debug("Reading upload state from log file: %s", log_file)

    with open(log_file) as fh:
        log_data = json.load(fh)

    uploaded = (
        "finished upload" if log_data["completed"] else "incomplete upload"
    )

    log.debug("state of run %s: %s", log_data["run_id"], uploaded)

    if not log_data["completed"]:
        log.debug(
            "total local files: %s | total uploaded files: %s | total failed"
            " upload: %s | total files to upload %s",
            log_data["total_local_files"],
            log_data["total_uploaded_files"],
            log_data["total_failed_upload"],
            log_data["total_local_files"] - log_data["total_uploaded_files"],
        )

    return log_data


def write_upload_state_to_log(
    run_id, run_path, log_file, local_files, uploaded_files, failed_files
) -> dict:
    """
    Write the log file for the run to log the state of what has been uploaded.

    If the uploaded files matches the local files with no failed uploads,
    the run is marked as complete uploaded. This is then used for future
    monitoring to know not to attempt re-upload.

    Log file will have the following structure:

    {
        "run_id": run_id,           -> ID of sequencing run
        "run path": run_path,       -> full local path to the run dir
        "completed": False,         -> if all files have uploaded
        "total_local_files": ,      -> total count of local files to upload
        "total_uploaded_files": 0,  -> total files already uploaded
        "total_failed_upload": 0,   -> total files failed to upload
        "failed_upload_files": [],  -> list of files previously failed upload
        "uploaded_files": {},       -> mapping of uploaded files to object ID
    }

    Parameters
    ----------
    run_id : str
        ID of sequencing run
    run_path : str
        path to run directory being uploaded
    log_file : str
        file to write log to
    local_files : list
        list of local files provided to upload
    uploaded_files : dict
        mapping of uploaded local file path to remote object ID
    failed_files : list
        list of files that failed to upload

    Returns
    -------
    dict
        all log data for the run
    """
    total_local_files = len(local_files)
    total_uploaded_files = len(uploaded_files.keys())
    total_failed_upload = len(failed_files)

    log.info("logging upload state of %s", run_id)
    log.info(
        "total local files: %s | total uploaded files: %s | total failed"
        " upload: %s",
        total_local_files,
        total_uploaded_files,
        total_failed_upload,
    )

    if os.path.exists(log_file):
        # log file already exists => continuing previous failed upload
        log.debug("log file already exists to update at %s", log_file)

        with open(log_file, "r") as fh:
            log_data = json.load(fh)
    else:
        log_data = {
            "run_id": run_id,
            "run_path": run_path,
            "completed": False,
            "total_local_files": total_local_files,
            "total_uploaded_files": 0,
            "total_failed_upload": 0,
            "failed_upload_files": [],
            "uploaded_files": {},
        }

    log_data["total_uploaded_files"] += total_uploaded_files
    log_data["total_failed_upload"] = total_failed_upload
    log_data["failed_upload_files"] = failed_files
    log_data["uploaded_files"] = {
        **log_data["uploaded_files"],
        **uploaded_files,
    }

    if (
        total_failed_upload == 0
        and total_local_files == log_data["total_uploaded_files"]
    ):
        log.info(
            "All local files uploaded and no files failed uploading, run"
            " completed uploading"
        )
        log_data["completed"] = True

    with open(log_file, "w") as fh:
        json.dump(log_data, fh, indent=4)

    return log_data
