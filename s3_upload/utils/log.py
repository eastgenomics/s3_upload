"""Functions to handle log streams"""

import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import sys


FORMATTER = logging.Formatter(
    "%(asctime)s [%(module)s] %(levelname)s: %(message)s"
)


def get_console_handler():
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(FORMATTER)
    return console_handler


def set_file_handler(logger, log_dir) -> None:
    """
    Set the file handler to redirect all logs to log file `s3_upload.log`
    in the specified directory

    Parameters
    ----------
    logger : logging.Logger
        logging handler
    log_dir : str
        path to where to write log file to
    """
    log_file = os.path.join(log_dir, "s3_upload.log")

    if any(
        [isinstance(x, TimedRotatingFileHandler) for x in logger.handlers]
    ) and os.path.exists(log_file):
        # log file handler already set and log file exists => use it
        logger.info("Log file handler already set to %s", log_file)
        return logger

    logger.info(
        "Initialised log fileHandler, setting log output to %s", log_file
    )

    check_write_permission_to_log_dir(log_dir)

    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=5
    )
    file_handler.setFormatter(FORMATTER)

    logger.addHandler(file_handler)

    return logger


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

    Raises
    ------
    AssertionError
        raised when the specified log directory is not writeable if it
        already exists
    """
    if logging.getLogger(logger_name).handlers:
        # logger already exists => use it
        return logging.getLogger(logger_name)

    log_file = os.path.join(log_dir, "s3_upload.log")
    check_write_permission_to_log_dir(log_dir)

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    Path(log_file).touch(exist_ok=True)

    logger = logging.getLogger(logger_name)

    if log_level:
        logger.setLevel(log_level)

    logger.addHandler(get_console_handler())
    logger.propagate = False

    return logger
