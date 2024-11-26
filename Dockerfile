FROM python:3.8-alpine

COPY requirements.txt requirements.txt

# - Install requirements
# - Delete unnecessary Python files
# - Alias command `s3_upload` to `python3 s3_upload.py` for convenience
RUN \
    pip install --quiet --upgrade pip && \
    pip install -r requirements.txt && \
    echo "Delete python cache directories" 1>&2 && \
    find /usr/local/lib/python3.8 \( -iname '*.c' -o -iname '*.pxd' -o -iname '*.pyd' -o -iname '__pycache__' \) | \
    xargs rm -rf {} && \
    echo "Setting s3_upload alias" 1>&2 && \
    printf '#!/bin/sh\npython3 /app/s3_upload/s3_upload.py "$@"'  > /usr/local/bin/s3_upload && \
    chmod +x /usr/local/bin/s3_upload

COPY . /app

WORKDIR /app/s3_upload

# display help if no args specified
CMD s3_upload --help
