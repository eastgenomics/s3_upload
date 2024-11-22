"""General utility functions"""

from datetime import datetime, timedelta
from glob import glob
from itertools import zip_longest
from os import path, scandir, stat
from pathlib import Path
import re
from typing import List, Tuple, Union

from .io import read_upload_state_log, read_samplesheet_from_run_directory
from .log import get_logger

log = get_logger("s3_upload")


def check_termination_file_exists(run_dir) -> bool:
    """
    Check if the run has completed sequencing from the presence of
    CopyComplete.txt (for NovaSeqs), or RTAComplete(.txt/.xml) for other
    types of Illumina sequencers.

    Adapted from: https://github.com/eastgenomics/dx-streaming-upload/blob/476b28af980ad62c5f2750cc0b6920b82a287b11/files/incremental_upload.py#L393

    Parameters
    ----------
    run_dir : str
        path to run directory to check

    Returns
    -------
    bool
        True if run is complete else False
    """
    log.debug("Checking for termination file in %s", run_dir)

    if path.exists(path.join(run_dir, "CopyComplete.txt")):
        # NovaSeq run that is complete
        log.debug("Termination file exists => sequencing complete")
        return True
    elif path.exists(path.join(run_dir, "RTAComplete.txt")) or path.exists(
        path.join(run_dir, "RTAComplete.xml")
    ):
        # other type of Illumina sequencer (e.g. MiSeq, NextSeq, HiSeq)
        log.debug("Termination file exists => sequencing complete")
        return True
    else:
        log.debug("Termination file does not exist => sequencing incomplete")
        return False


def check_is_sequencing_run_dir(run_dir) -> bool:
    """
    Check if a given directory is a sequencing run from presence of
    RunInfo.xml file

    Parameters
    ----------
    run_dir : str
        path to directory to check

    Returns
    -------
    bool
        True if directory is a sequencing run else False
    """
    log.debug("Checking if directory is a sequencing run: %s", run_dir)
    return path.exists(path.join(run_dir, "RunInfo.xml"))


def check_run_age_within_limit(run_dir, max_age=72) -> bool:
    """
    Check if the run age is within the max_age limit to consider for upload.

    Run age is determined from the mtime of the RunInfo.xml file in the
    given run directory. Since unix has no created time stored for a file
    we use the last modified time, which for a regular sequencing output
    is when it was created. This has the side effect that an older run may
    be triggered for upload by touching this file.

    Parameters
    ----------
    run_dir : str
        path to directory to check
    max_age : int
        maximum age in hours to monitor a run directory for uploading

    Returns
    -------
    bool
        True if modified time of RunInfo.xml is within allowed max_age
    """
    now = datetime.now()
    modified_time = datetime.fromtimestamp(
        path.getmtime(path.join(run_dir, "RunInfo.xml"))
    )

    if modified_time > now - timedelta(hours=int(max_age)):
        return True
    else:
        return False


def check_upload_state(
    run_dir, log_dir="/var/log/s3_upload/"
) -> Tuple[str, list]:
    """
    Checking upload state of run (i.e. uploaded, partial, new)

    Parameters
    ----------
    run_dir : str
        name of run directory to check upload state
    log_dir : str
        directory where to read per run upload log files from

    Returns
    -------
    str
        state of run upload, will be one of: uploaded, partial or new
    list
        list of uploaded files
    """
    upload_log = path.join(
        log_dir, "uploads/", f"{Path(run_dir).name}.upload.log.json"
    )

    if not path.exists(upload_log):
        return "new", []

    log_contents = read_upload_state_log(log_file=upload_log)

    # get the list of already uploaded files from the stored mapping
    uploaded_files = list(log_contents["uploaded_files"].keys())

    if log_contents["completed"]:
        return "uploaded", uploaded_files
    else:
        return "partial", uploaded_files


def check_all_uploadable_samples(
    samplesheet_contents, sample_pattern
) -> Union[bool, None]:
    """
    Check if all samples in samplesheet match the provided regex pattern.

    This is to determine if the run is one to be uploaded against the
    provided pattern(s) from the config file.

    Parameters
    ----------
    samplesheet_contents : list
        contents of samplesheet
    sample_pattern : str
        regex pattern for matching against samplenames

    Returns
    -------
    bool | None
        True if all sample names match the regex, else False. None returned
        if no samplenames are returned from the call to
        get_samplenames_from_samplesheet.
    """
    log.info(
        "Checking if sample names match provided pattern(s) from config: %s",
        sample_pattern,
    )

    sample_names = get_samplenames_from_samplesheet(
        contents=samplesheet_contents
    )

    if not sample_names:
        log.warning("Failed parsing samplenames from samplesheet")
        return None

    all_match = all([re.search(sample_pattern, x) for x in sample_names])

    if all_match:
        log.info("All samples in samplesheet match provided regex pattern")
        return True
    else:
        log.info(
            "One or more samples in samplsheet did not match provided regex. "
            "Run will not be uploaded."
        )
        return False


def get_runs_to_upload(
    monitor_dirs,
    log_dir="/var/log/s3_upload",
    sample_pattern=None,
    max_age=None,
) -> Tuple[list, dict]:
    """
    Get completed sequencing runs to upload from specified directories
    to monitor

    Parameters
    ----------
    monitor_dirs : list
        list of directories to check for completed sequencing runs
    log_dir : str
        directory where to read per run upload log files from
    sample_pattern : str
        optional regex pattern that all samples from samplesheet must
        match to be uploadable
    max_age : int
        maximum age in hours to monitor a run directory for uploading

    Returns
    -------
    list
        list of directories that are completed runs not yet uploaded
    dict
        mapping of directories that have been partially uploaded and
        the uploaded files
    """
    to_upload = []
    partially_uploaded = {}

    for monitored_dir in monitor_dirs:
        # check each sub directory if it looks like a sequencing run,
        # if it has completed and if it has been uploaded
        log.info("Checking %s for completed sequencing runs", monitored_dir)

        sub_directories = [
            f.path for f in scandir(monitored_dir) if f.is_dir()
        ]

        log.debug(
            "directories found in %s: %s",
            monitored_dir,
            [Path(x).name for x in sub_directories],
        )

        for sub_dir in sub_directories:
            if not check_is_sequencing_run_dir(sub_dir):
                log.debug(
                    "%s is not a sequencing run and will not be uploaded",
                    sub_dir,
                )
                continue

            if not check_termination_file_exists(sub_dir):
                log.debug(
                    "%s has not completed sequencing and will not be uploaded",
                    sub_dir,
                )
                continue

            if not check_run_age_within_limit(sub_dir):
                log.debug(
                    "%s older than maximum age to monitor for upload and will"
                    " not be uploaded",
                    sub_dir,
                )
                continue

            samplesheet_contents = read_samplesheet_from_run_directory(sub_dir)

            if not samplesheet_contents:
                log.error(
                    "Failed parsing samplesheet from %s, run will not be"
                    " uploaded",
                    sub_dir,
                )
                continue

            if sample_pattern:
                if not check_all_uploadable_samples(
                    samplesheet_contents=samplesheet_contents,
                    sample_pattern=sample_pattern,
                ):
                    continue

            upload_state, uploaded_files = check_upload_state(
                run_dir=sub_dir, log_dir=log_dir
            )

            if upload_state == "uploaded":
                log.info(
                    "%s has completed uploading and will be skipped", sub_dir
                )
                continue
            elif upload_state == "partial":
                log.info(
                    "%s has partially uploaded (%s files), will continue"
                    " uploading",
                    sub_dir,
                    len(uploaded_files),
                )
                partially_uploaded[sub_dir] = uploaded_files
            else:
                log.info(
                    "%s has not started uploading, to be uploaded", sub_dir
                )
                to_upload.append(sub_dir)

    return to_upload, partially_uploaded


def get_sequencing_file_list(seq_dir, exclude_patterns=None) -> list:
    """
    Recursively get list of files and their paths from the given
    directory.

    Files are returned in order of their file size without the given
    root `seq_dir`.

    Parameters
    ----------
    seq_dir : str
        path to search for files
    exclude_patterns : list
        list of regex patterns against which to exclude files, matching
        against the full file path and file name

    Returns
    -------
    list
        sorted list of files by their file size (descending)
    """
    log.info("Getting list of files to upload in %s", seq_dir)
    files = sorted(
        [
            (x, stat(x).st_size)
            for x in glob(f"{seq_dir}/**/*", recursive=True)
            if Path(x).is_file()
        ],
        key=lambda x: x[1],
        reverse=True,
    )

    log.info("Found %s files in %s", len(files), seq_dir)

    if exclude_patterns:
        log.info(
            "Excluding files to upload against provided pattern(s): %s",
            exclude_patterns,
        )
        files = [
            x
            for x in files
            if not re.search(r"|".join(exclude_patterns), x[0])
        ]

    total_size = sizeof_fmt(sum(x[1] for x in files))

    log.info(f"{len(files)} files found to upload totalling %s", total_size)

    return [x[0] for x in files]


def get_samplenames_from_samplesheet(contents) -> Union[list, None]:
    """
    Parses the samplenames from the provided samplesheet contents

    Parameters
    ----------
    contents : list
        contents of samplesheet

    Returns
    -------
    list | None
        list of samplenames if able to be parsed, else None
    """
    log.debug("Parsing sample names from samplesheet")
    first_sample_index = [
        contents.index(l) + 1 for l in contents if l.startswith("Sample_ID")
    ]

    if not len(first_sample_index) == 1:
        log.warning(
            "Samplesheet does not contain a unique Sample_ID line to parse"
            " sample list from. Sample ID found at: %s",
            first_sample_index,
        )
        return None

    sample_lines = contents[first_sample_index[0] :]
    sample_names = [x.split(",")[0] for x in sample_lines]

    log.debug("Parsed %s samplenames: %s", len(sample_names), sample_names)

    return sample_names


def filter_uploaded_files(local_files, uploaded_files) -> list:
    """
    Remove already uploaded files from list of local files to upload

    Parameters
    ----------
    local_files : list
        list of local files to upload
    uploaded_files : list
        list of files already uploaded

    Returns
    -------
    list
        list of files not yet uploaded
    """
    log.info("removing already uploaded files from local file list")
    log.debug(
        "total local files: %s | total uploaded files: %s",
        len(local_files),
        len(uploaded_files),
    )

    uploadable_files = list(set(local_files) - set(uploaded_files))

    log.debug("%s local files left to upload", len(uploadable_files))

    return uploadable_files


def split_file_list_by_cores(files, n) -> List[List[str]]:
    """
    Split given list of files sorted by file size into n approximately
    equal total size and length.

    This is a reasonably naive approach to give us n lists of files with
    an equal length and approximately equal split of small to large files,
    allowing us to more evenly split the total amount of data to upload
    between each ProcessPool.

    Parameters
    ----------
    files : list
        sorted list of files
    n : int
        number of sub lists to split file list to

    Returns
    ------
    list
        list of lists of filenames
    """
    files = [files[i : i + n] for i in range(0, len(files), n)]
    files = [[x for x in y if x] for y in zip_longest(*files)]

    return files


def verify_config(config) -> None:
    """
    Verify that config structure and parameters are valid

    Parameters
    ----------
    config : dict
        contents of config file to check
    """
    log.debug(
        "Verifying contents of config are valid, contents parsed: %s", config
    )
    errors = []

    if not isinstance(config.get("max_cores", 0), int):
        errors.append("max_cores must be an integer")

    if not isinstance(config.get("max_threads", 0), int):
        errors.append("max_threads must be an integer")

    if not config.get("log_dir"):
        errors.append("required parameter log_dir not defined")

    if not config.get("monitor"):
        errors.append("required parameter monitor not defined")

    for idx, monitor in enumerate(config.get("monitor", "")):
        for key, expected_type in {
            "monitored_directories": list,
            "bucket": str,
            "remote_path": str,
        }.items():
            if not monitor.get(key):
                errors.append(
                    f"required parameter {key} missing from monitor section"
                    f" {idx}"
                )
            else:
                if not isinstance(monitor.get(key), expected_type):
                    errors.append(
                        f"{key} not of expected type from monitor section "
                        f"{idx}. Expected: {expected_type} | Found "
                        f"{type(monitor.get(key))}"
                    )

        if monitor.get("sample_regex"):
            try:
                re.compile(monitor.get("sample_regex"))
            except Exception:
                errors.append(
                    "Invalid regex pattern provided in monitor section "
                    f"{idx}: {monitor.get('sample_regex')}"
                )

    if errors:
        error_message = (
            f"{len(errors)} errors found in config:{chr(10)}{chr(9)}"
            f"{f'{chr(10)}{chr(9)}'.join(errors)}"
        )
        log.error(error_message)
        raise RuntimeError(error_message)
    else:
        log.debug("Config valid")


def sizeof_fmt(num) -> str:
    """
    Function to turn bytes to human readable file size format.

    Taken from https://stackoverflow.com/questions/1094841/get-human-readable-version-of-file-size

    Parameters
    ----------
    num : int
        total size in bytes

    Returns
    -------
    str
        file size in human-readable format
    """
    for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
        if abs(num) < 1024.0:
            return f"{num:3.2f}{unit}B"
        num /= 1024.0
    return f"{num:.2f}YiB"
