## End to End Tests

A comprehensive suite of automated end to end tests are included here to test the behaviour in multiple different scenarios (i.e multiple runs to upload, filtering by regex patterns, interrupting and resuming etc). For each test scenario, a local test run directory structure is created and an accompanying config file generated, these are then provided to run the main entry point in monitor mode to allow the upload to run. On completing, the end point behaviour is then tested through multiple tests, and both the local remote files cleaned up before exiting.

To run these tests the following is required:
* ability to authenticate to AWS via SSO or access keys (see [AWS authentication](https://github.com/eastgenomics/s3_upload?tab=readme-ov-file#authentication) in the readme)
* a test AWS S3 bucket defined to the environment variable `E2E_TEST_S3_BUCKET`

Tests may then be run with the following command `python3 -m pytest tests/e2e/`.