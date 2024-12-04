"""Functions to handle log streams"""

from datetime import date, datetime, time, timedelta
from glob import glob
import logging
from logging import FileHandler
import os
from pathlib import Path
import re
import sys


FORMATTER = logging.Formatter(
    "%(asctime)s [%(module)s] %(levelname)s: %(message)s"
)


def get_console_handler() -> logging.StreamHandler:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(FORMATTER)
    return console_handler


def set_file_handler(logger, log_dir) -> logging.Logger:
    """
    Set the file handler to redirect all logs to log file
    `s3_upload.log.{%Y-%m-%d}`
    in the specified directory

    Parameters
    ----------
    logger : logging.Logger
        logging handler
    log_dir : str
        path to where to write log file to

    Returns
    -------
    logging.Logger
        logging handler
    """
    check_write_permission_to_log_dir(log_dir)

    log_file = os.path.join(
        log_dir, f"s3_upload.log.{datetime.now().strftime('%Y-%m-%d')}"
    )

    existing_file_handler = [
        x for x in logger.handlers if isinstance(x, FileHandler)
    ]

    if existing_file_handler and os.path.exists(log_file):
        logger.debug("FileHandler already set")
        return logger

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    Path(log_file).touch(exist_ok=True)

    file_handler = FileHandler(filename=log_file)
    file_handler.setFormatter(FORMATTER)

    logger.addHandler(file_handler)

    logger.info(
        "Initialised log FileHandler, setting log output to %s", log_file
    )

    clear_old_logs(logger=logger, log_dir=log_dir, backup_count=5)

    return logger


def clear_old_logs(logger, log_dir, backup_count) -> None:
    """
    Ensures that at most `backup_count` log file backups are retained.

    To be called on initialising the log file handler which sets the log
    output to a file with the current datetime stamp as suffix, this will
    be used to delete files older than `backup_count` days.

    Parameters
    ----------
    logger : logging.Logger
        handle to the logger
    log_dir : str
        path to log directory
    backup_count : int
    """
    oldest_backup_date = datetime.combine(
        (date.today() - timedelta(days=backup_count)), time()
    )

    backup_files = [
        (f, re.search(r"\d{4}-\d{2}-\d{2}$|$", f).group())
        for f in glob(f"{log_dir}/s3_upload.log.*")
    ]
    old_backup_files = [
        x[0]
        for x in backup_files
        if x[1] and datetime.strptime(x[1], "%Y-%m-%d") < oldest_backup_date
    ]

    logger.debug(
        "%s old backup files to be removed: %s",
        len(old_backup_files),
        old_backup_files,
    )

    if old_backup_files:
        for old_file in old_backup_files:
            try:
                os.remove(old_file)
            except OSError:
                logger.exception("Failed to delete old log file %s", old_file)


def check_write_permission_to_log_dir(log_dir) -> None:
    """
    Check that the given log dir, or highest parent dir that exists, is
    writable

    Parameters
    ----------
    log_dir : str
        path to log dir

    Raises
    ------
    PermissionError
        Raised if path supplied is not writable
    """
    while log_dir:
        if not os.path.exists(log_dir):
            log_dir = Path(log_dir).parent
            continue

        if not os.access(log_dir, os.W_OK):
            raise PermissionError(
                f"Path to provided log directory {log_dir} does not appear to"
                " have write permission for current user"
            )
        else:
            return


def get_logger(
    logger_name, log_level=logging.INFO, log_dir="/var/log/s3_upload"
) -> logging.Logger:
    """
    Initialise the logger

    Parameters
    ----------
    logger_name : str
        name of the logger to intialise
    log_level : str
        level of logging to set
    log_dir : str
        path to where to write log files to

    Returns
    -------
    logging.Logger
        handle to configured logger
    """
    if logging.getLogger(logger_name).handlers:
        # logger already exists => use it
        return logging.getLogger(logger_name)

    logger = logging.getLogger(logger_name)

    if log_level:
        logger.setLevel(log_level)

    logger.addHandler(get_console_handler())
    logger.propagate = False

    return logger
