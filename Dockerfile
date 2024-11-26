FROM python:3.8-alpine

COPY requirements.txt requirements.txt

# - Install gcc and dependencies (required for compiling psutil)
# - Install Python requirements
# - Delete unnecessary Python cache files and remove gcc et al.
# - Alias command `s3_upload` to `python3 s3_upload.py` for convenience
RUN \
    apk add --no-cache gcc musl-dev linux-headers && \
    pip install --quiet --upgrade pip && \
    pip install -r requirements.txt && \

    echo "Deleting cache files and removing build dependencies" 1>&2 && \
    find /usr/local/lib/python3.8  \( -iname '*.c' -o -iname '*.pxd' -o -iname '*.pyd' -o -iname '__pycache__' \) | \
    xargs rm -rf {} && \
    rm -rf /root/.cache/pip && \
    apk --purge del gcc musl-dev linux-headers && \

    echo "Setting s3_upload alias" 1>&2 && \
    printf '#!/bin/sh\npython3 /app/s3_upload/s3_upload.py "$@"'  > /usr/local/bin/s3_upload && \
    chmod +x /usr/local/bin/s3_upload

COPY . /app

WORKDIR /app/s3_upload

# display help if no args specified
CMD s3_upload --help
