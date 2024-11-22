import argparse
from os import cpu_count, makedirs, path
from pathlib import Path
import sys
from timeit import default_timer as timer

from utils.io import (
    acquire_lock,
    read_config,
    release_lock,
    write_upload_state_to_log,
)
from utils.upload import (
    check_aws_access,
    check_buckets_exist,
    multi_core_upload,
)
from utils.utils import (
    check_is_sequencing_run_dir,
    check_termination_file_exists,
    get_runs_to_upload,
    get_sequencing_file_list,
    filter_uploaded_files,
    split_file_list_by_cores,
    verify_config,
)
from utils.log import get_logger, set_file_handler
from utils import slack


log = get_logger("s3_upload")


def parse_args() -> argparse.Namespace:
    """
    Parse cmd line arguments

    Returns
    -------
    argparse.Namespace
        parsed arguments
    """
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers(
        help="Upload mode to run", dest="mode", required=True
    )

    monitor_parser = subparsers.add_parser(
        "monitor",
        help=(
            "Mode to be run on a schedule to monitor directories for newly"
            " completed sequencing runs"
        ),
    )
    monitor_parser.add_argument(
        "--config",
        required=True,
        help="Config file for monitoring directories to upload",
    )
    monitor_parser.add_argument(
        "--dry_run",
        default=False,
        action="store_true",
        help=(
            "Calls everything except the actual upload to check what runs"
            " would be uploaded"
        ),
    )

    upload_parser = subparsers.add_parser(
        "upload",
        help="Mode to upload a single directory to given location in S3",
    )
    upload_parser.add_argument(
        "--local_path", help="path to directory to upload"
    )
    upload_parser.add_argument(
        "--bucket",
        type=str,
        help="S3 bucket to upload to",
    )
    upload_parser.add_argument(
        "--remote_path",
        default="/",
        help="Remote path in bucket to upload sequencing dir to",
    )
    upload_parser.add_argument(
        "--skip_check",
        default=False,
        action="store_true",
        help=(
            "Controls if to skip checks for the provided directory being a"
            " completed sequencing run. This allows for uploading any"
            " arbitrary provided directory to AWS S3."
        ),
    )
    upload_parser.add_argument(
        "--cores",
        required=False,
        type=int,
        default=cpu_count(),
        help=(
            "Number of CPU cores to split total files to upload across, will "
            "default to using all available"
        ),
    )
    upload_parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help=(
            "Number of threads to open per core to split uploading across "
            "(default: 8)"
        ),
    )

    return parser.parse_args()


def upload_single_run(args):
    """
    Upload provided single run directory into AWS S3

    Parameters
    ----------
    args : argparse.NameSpace
        parsed command line arguments
    """
    check_aws_access()
    check_buckets_exist(buckets=[args.bucket])

    if not args.skip_check:
        log.info(
            "Checking if the provided directory is a complete sequencing run"
        )
        if not check_is_sequencing_run_dir(
            args.local_path
        ) or not check_termination_file_exists(args.local_path):
            log.error(
                "Provided directory: %s does not appear to be a complete "
                "sequencing run. Please check the provided path and try"
                " again.",
                args.local_path,
            )
            exit()

    files = get_sequencing_file_list(args.local_path)
    files = split_file_list_by_cores(files=files, n=args.cores)

    # pass through the parent of the specified directory to upload
    # to ensure we upload into the actual run directory
    parent_path = Path(args.local_path).parent

    # simple timer of upload
    start = timer()

    multi_core_upload(
        files=files,
        bucket=args.bucket,
        remote_path=args.remote_path,
        cores=args.cores,
        threads=args.threads,
        parent_path=parent_path,
    )

    end = timer()
    total = end - start

    log.info(
        "Uploaded %s in %s",
        args.local_path,
        f"{int(total // 60)}m {int(total % 60)}s",
    )


def monitor_directories_for_upload(config, dry_run):
    """
    Monitor specified directories for complete sequencing runs to upload

    Parameters
    ----------
    config : dict
        contents of config file
    dry_run : bool
        calls everything except the actual upload for testing / debugging
    """
    log.info("Beginning monitoring directories for runs to upload")

    # preferentially use respective log and alert channel webhooks if specified
    log_url = config.get("slack_log_webhook") or config.get(
        "slack_alert_webhook"
    )
    alert_url = config.get("slack_alert_webhook") or config.get(
        "slack_log_webhook"
    )

    if not log_url and not alert_url:
        log.warning(
            "Neither `slack_log_webhook` or `slack_alert_webhook` specified =>"
            " no Slack notifications will be sent"
        )

    check_aws_access(slack_alert_webhook=alert_url)
    check_buckets_exist(
        buckets=set([x["bucket"] for x in config["monitor"]]),
        slack_alert_webhook=alert_url,
    )

    cores = config.get("max_cores", cpu_count)
    threads = config.get("max_threads", 4)
    log_dir = config.get("log_dir", "/var/log/s3_upload")

    to_upload = []
    partially_uploaded = []

    # find all the runs to upload in the specified monitored directories
    # and build a dict per run with config of where to upload
    for monitor_dir_config in config["monitor"]:
        completed_runs, incomplete_runs = get_runs_to_upload(
            monitor_dir_config.get("monitored_directories"),
            log_dir=log_dir,
            sample_pattern=monitor_dir_config.get("sample_regex"),
            max_age=config.get("max_age", 72),
        )

        for run_dir in completed_runs:
            to_upload.append(
                {
                    "run_dir": run_dir,
                    "run_id": Path(run_dir).name,
                    "parent_path": Path(run_dir).parent,
                    "bucket": monitor_dir_config["bucket"],
                    "remote_path": monitor_dir_config["remote_path"],
                    "exclude_patterns": monitor_dir_config.get(
                        "exclude_patterns"
                    ),
                }
            )

        for partial_run, uploaded_files in incomplete_runs.items():
            partially_uploaded.append(
                {
                    "run_dir": partial_run,
                    "run_id": Path(partial_run).name,
                    "parent_path": Path(partial_run).parent,
                    "bucket": monitor_dir_config["bucket"],
                    "remote_path": monitor_dir_config["remote_path"],
                    "uploaded_files": uploaded_files,
                }
            )

    if not to_upload and not partially_uploaded:
        log.info("No sequencing runs requiring upload found. Exiting now.")
        sys.exit(0)

    log.info(
        "Found %s new sequencing runs to upload: %s",
        len(to_upload),
        ", ".join([x["run_id"] for x in to_upload]),
    )
    if partially_uploaded:
        log.info(
            "Found %s partially uploaded runs to continue uploading: %s",
            len(partially_uploaded),
            ", ".join([x["run_id"] for x in partially_uploaded]),
        )

        # preferentially upload partial runs first
        to_upload = partially_uploaded + to_upload

    if dry_run:
        for run in to_upload:
            log.info(
                "%s would be uploaded to %s:%s",
                run["run_dir"],
                run["bucket"],
                path.join(run["remote_path"], run["run_id"]),
            )
        log.info("--dry_run specified, exiting now without uploading")
        sys.exit()

    log.info("Beginning upload of %s runs", len(to_upload))

    runs_successfully_uploaded = []
    runs_failed_upload = []

    for idx, run_config in enumerate(to_upload, 1):
        # begin uploading of each sequencing run
        log.info(
            "Uploading run %s [%s/%s]",
            run_config["run_id"],
            idx,
            len(to_upload),
        )

        # simple timer to log total upload time
        start = timer()

        all_run_files = get_sequencing_file_list(
            seq_dir=run_config["run_dir"],
            exclude_patterns=run_config.get("exclude_patterns"),
        )

        files_to_upload = all_run_files.copy()

        if run_config.get("uploaded_files"):
            # files we stored from a previous partial upload to not reupload
            files_to_upload = filter_uploaded_files(
                local_files=files_to_upload,
                uploaded_files=run_config.get("uploaded_files"),
            )

        files_to_upload = split_file_list_by_cores(
            files=files_to_upload, n=cores
        )

        # call the actual upload, any errors being raised that result in
        # files not being uploaded should be returned and will result in
        # upload state log storing the run as not complete, allowing for
        # retries on uploading
        uploaded_files, failed_upload = multi_core_upload(
            files=files_to_upload,
            bucket=run_config["bucket"],
            remote_path=run_config["remote_path"],
            cores=cores,
            threads=threads,
            parent_path=run_config["parent_path"],
        )

        # set output logs to go into subdirectory with stdout/stderr log
        makedirs(path.join(log_dir, "uploads"), exist_ok=True)
        run_log_file = path.join(
            log_dir, f"uploads/{run_config['run_id']}.upload.log.json"
        )

        log_data = write_upload_state_to_log(
            run_id=run_config["run_id"],
            run_path=run_config["run_dir"],
            log_file=run_log_file,
            local_files=all_run_files,
            uploaded_files=uploaded_files,
            failed_files=failed_upload,
        )

        if log_data["completed"]:
            upload_state = "Fully"
            runs_successfully_uploaded.append(log_data["run_id"])
        else:
            upload_state = "Partially"
            runs_failed_upload.append(log_data["run_id"])

        end = timer()
        total = end - start

        log.info(
            "%s uploaded %s in %s",
            upload_state,
            run_config["run_id"],
            f"{int(total // 60)}m {int(total % 60)}s",
        )

    log.info(
        "Completed uploading all runs, %s successfully uploaded, %s failed"
        " upload",
        len(runs_successfully_uploaded),
        len(runs_failed_upload),
    )

    if runs_successfully_uploaded and log_url:
        log.debug(
            "Sending success upload message to Slack channel %s", log_url
        )
        message = slack.format_message(completed=runs_successfully_uploaded)
        slack.post_message(url=log_url, message=message)

    if runs_failed_upload and alert_url:
        log.debug("Sending failed upload alert to Slack channel %s", alert_url)
        message = slack.format_message(failed=runs_failed_upload)
        slack.post_message(url=alert_url, message=message)


def main() -> None:
    args = parse_args()

    if args.mode == "upload":
        upload_single_run(args)
    else:
        config = read_config(config=args.config)
        verify_config(config=config)

        log_dir = config.get("log_dir", "/var/log/s3_upload")
        lock_fd = acquire_lock(lock_file=path.join(log_dir, "s3_upload.lock"))

        if config.get("log_level"):
            log.setLevel(config.get("log_level"))

        log.info("this is main info")
        log.debug("this is main debug")

        set_file_handler(log, log_dir=log_dir)

        monitor_directories_for_upload(config=config, dry_run=args.dry_run)

        release_lock(lock_fd)


if __name__ == "__main__":
    main()
