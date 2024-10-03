import os
from shutil import rmtree
import unittest

from tests import TEST_DATA_DIR

from s3_upload.utils import utils


class TestCheckTerminationFileExists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_run_dir = os.path.join(TEST_DATA_DIR, "test_run")
        os.makedirs(
            cls.test_run_dir,
            exist_ok=True,
        )

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.test_run_dir)

    def test_complete_novaseq_run_returns_true(self):
        """
        Check complete NovaSeq runs correctly identified from
        CopyComplete.txt file in the run directory
        """
        termination_file = os.path.join(self.test_run_dir, "CopyComplete.txt")
        open(termination_file, "w").close()

        with self.subTest("Complete NovaSeq run identified"):
            self.assertTrue(
                utils.check_termination_file_exists(self.test_run_dir)
            )

        os.remove(termination_file)

    def test_complete_non_novaseq_run_returns_true(self):
        """
        Check other completed non-NovaSeq runs correctly identified from
        RTAComplete.txt or RTAComplete.xml files
        """
        for suffix in ["txt", "xml"]:
            termination_file = os.path.join(
                self.test_run_dir, f"RTAComplete.{suffix}"
            )

            open(termination_file, "w").close()

            with self.subTest("Checking RTAComplete.txt"):
                self.assertTrue(
                    utils.check_termination_file_exists(self.test_run_dir)
                )

            os.remove(termination_file)

    def test_incomplete_sequencing_run_returns_false(self):
        """
        Check incomoplete runs correctly identified
        """
        self.assertFalse(
            utils.check_termination_file_exists(self.test_run_dir)
        )


class TestCheckIsSequencingRunDir(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_run_dir = os.path.join(TEST_DATA_DIR, "test_run")
        os.makedirs(
            cls.test_run_dir,
            exist_ok=True,
        )

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.test_run_dir)

    def test_non_sequencing_run_dir_returns_false(self):
        # no RunInfo.xml file present in test_data dir => not a run
        utils.check_is_sequencing_run_dir(self.test_run_dir)

    def test_check_sequencing_run_dir_returns_true(self):
        run_info_xml = os.path.join(self.test_run_dir, "RunInfo.xml")
        open(run_info_xml, "w").close()

        with self.subTest("RunInfo.xml exists"):
            utils.check_is_sequencing_run_dir(self.test_run_dir)

        os.remove(run_info_xml)


class TestGetSequencingFileList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """
        Set up a reasonable approximation of a sequencing dir strcuture with
        files of differing sizes
        """
        cls.sequencing_dir_paths = [
            "Data/Intensities/BaseCalls/L001/C1.1",
            "Data/Intensities/BaseCalls/L002/C1.1",
            "Thumbnail_Images/L001/C1.1",
            "Thumbnail_Images/L002/C1.1",
            "InterOp/C1.1",
            "Logs",
        ]

        cls.sequencing_files = [
            ("Data/Intensities/BaseCalls/L001/C1.1/L001_2.cbcl", 232012345),
            ("Data/Intensities/BaseCalls/L002/C1.1/L002_2.cbcl", 232016170),
            ("Thumbnail_Images/L001/C1.1/s_1_2103_green.png", 69551),
            ("Thumbnail_Images/L002/C1.1/s_1_2103_red.png", 54132),
            ("InterOp/C1.1/BasecallingMetricsOut.bin", 13731),
            ("Logs/240927_A01295_0425_AHJWGFDRX5_Cycle0_Log.00.log", 5243517),
        ]

        cls.test_run_dir = os.path.join(TEST_DATA_DIR, "test_run")

        for sub_dir in cls.sequencing_dir_paths:
            os.makedirs(
                os.path.join(cls.test_run_dir, sub_dir),
                exist_ok=True,
            )

        for seq_file, size in cls.sequencing_files:
            with open(os.path.join(cls.test_run_dir, seq_file), "wb") as f:
                # create test file of given size without actually
                # writing any data to disk
                f.truncate(size)

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.test_run_dir)

    def test_files_returned_in_sorted_order_by_file_size(self):
        expected_file_list = [
            "Data/Intensities/BaseCalls/L002/C1.1/L002_2.cbcl",
            "Data/Intensities/BaseCalls/L001/C1.1/L001_2.cbcl",
            "Logs/240927_A01295_0425_AHJWGFDRX5_Cycle0_Log.00.log",
            "Thumbnail_Images/L001/C1.1/s_1_2103_green.png",
            "Thumbnail_Images/L002/C1.1/s_1_2103_red.png",
            "InterOp/C1.1/BasecallingMetricsOut.bin",
        ]

        returned_file_list = utils.get_sequencing_file_list(
            seq_dir=self.test_run_dir
        )

        self.assertEqual(returned_file_list, expected_file_list)

    def test_empty_directories_ignored_and_only_files_returned(self):
        empty_dir = os.path.join(self.test_run_dir, "empty_dir")
        os.makedirs(
            empty_dir,
            exist_ok=True,
        )

        with self.subTest("empty directory ignored"):
            returned_file_list = utils.get_sequencing_file_list(
                seq_dir=self.test_run_dir
            )
            # just test we get back the same files and ignore their ordering
            self.assertEqual(
                sorted(returned_file_list),
                sorted(x[0] for x in self.sequencing_files),
            )

        rmtree(empty_dir)

    def test_exclude_patterns_removes_matching_files(self):
        """
        Test that both patterns of filenames and / or directory names
        correctly excludes expected files from the returned file list
        """
        exclude_patterns_matched_files = [
            (
                [".*png$"],
                [
                    "Data/Intensities/BaseCalls/L002/C1.1/L002_2.cbcl",
                    "Data/Intensities/BaseCalls/L001/C1.1/L001_2.cbcl",
                    "Logs/240927_A01295_0425_AHJWGFDRX5_Cycle0_Log.00.log",
                    "InterOp/C1.1/BasecallingMetricsOut.bin",
                ],
            ),
            (
                [".*log$"],
                [
                    "Data/Intensities/BaseCalls/L002/C1.1/L002_2.cbcl",
                    "Data/Intensities/BaseCalls/L001/C1.1/L001_2.cbcl",
                    "Thumbnail_Images/L001/C1.1/s_1_2103_green.png",
                    "Thumbnail_Images/L002/C1.1/s_1_2103_red.png",
                    "InterOp/C1.1/BasecallingMetricsOut.bin",
                ],
            ),
            (
                [".*png$", ".*log$"],
                [
                    "Data/Intensities/BaseCalls/L002/C1.1/L002_2.cbcl",
                    "Data/Intensities/BaseCalls/L001/C1.1/L001_2.cbcl",
                    "InterOp/C1.1/BasecallingMetricsOut.bin",
                ],
            ),
            (
                ["Logs/"],
                [
                    "Data/Intensities/BaseCalls/L002/C1.1/L002_2.cbcl",
                    "Data/Intensities/BaseCalls/L001/C1.1/L001_2.cbcl",
                    "Thumbnail_Images/L001/C1.1/s_1_2103_green.png",
                    "Thumbnail_Images/L002/C1.1/s_1_2103_red.png",
                    "InterOp/C1.1/BasecallingMetricsOut.bin",
                ],
            ),
            (
                ["Thumbnail_Images/"],
                [
                    "Data/Intensities/BaseCalls/L002/C1.1/L002_2.cbcl",
                    "Data/Intensities/BaseCalls/L001/C1.1/L001_2.cbcl",
                    "Logs/240927_A01295_0425_AHJWGFDRX5_Cycle0_Log.00.log",
                    "InterOp/C1.1/BasecallingMetricsOut.bin",
                ],
            ),
        ]

        for patterns, expected_files in exclude_patterns_matched_files:
            with self.subTest("files correctly excluded by pattern(s)"):
                returned_file_list = utils.get_sequencing_file_list(
                    seq_dir=self.test_run_dir, exclude_patterns=patterns
                )

                self.assertEqual(
                    sorted(returned_file_list), sorted(expected_files)
                )


class TestSplitFileListByCores(unittest.TestCase):
    items = [1, 2, 3, 4, 5, 6, 7, 8, 100, 110, 120, 130, 140, 150, 160, 170]

    def test_list_split_as_expected(self):

        returned_split_list = utils.split_file_list_by_cores(
            files=self.items, n=4
        )

        expected_list = [
            [1, 5, 100, 140],
            [2, 6, 110, 150],
            [3, 7, 120, 160],
            [4, 8, 130, 170],
        ]

        self.assertEqual(returned_split_list, expected_list)

    def test_correct_return_when_file_length_not_exact_multiple_of_n(self):
        returned_split_list = utils.split_file_list_by_cores(
            files=self.items, n=3
        )

        expected_list = [
            [1, 4, 7, 110, 140, 170],
            [2, 5, 8, 120, 150],
            [3, 6, 100, 130, 160],
        ]

        self.assertEqual(returned_split_list, expected_list)

    def test_error_not_raised_when_n_greater_than_total_files(self):
        returned_split_list = utils.split_file_list_by_cores(files=[1, 2], n=3)

        self.assertEqual([[1], [2]], returned_split_list)

    def test_empty_file_list_returns_empty_list(self):
        returned_split_list = utils.split_file_list_by_cores(files=[], n=2)

        self.assertEqual([], returned_split_list)
