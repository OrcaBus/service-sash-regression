from unittest.mock import MagicMock, patch

from comparator.s3_utils import download_s3_dir, parse_s3_uri, upload_file


class TestParseS3Uri:
    def test_returns_bucket_and_key(self):
        bucket, key = parse_s3_uri("s3://my-bucket/path/to/file.txt")
        assert bucket == "my-bucket"
        assert key == "path/to/file.txt"

    def test_prefix_with_trailing_slash(self):
        bucket, key = parse_s3_uri("s3://my-bucket/prefix/")
        assert bucket == "my-bucket"
        assert key == "prefix/"


class TestDownloadS3Dir:
    def _make_mock_s3(self, keys: list[str], prefix: str):
        mock_s3 = MagicMock()
        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{"Contents": [{"Key": k} for k in keys]}]
        return mock_s3

    def test_downloads_objects_into_local_dir(self, tmp_path):
        prefix = "sash/0.6.4/SEQC-II-medium/"
        key = f"{prefix}CANCER_REPORT/file.txt"
        mock_s3 = self._make_mock_s3([key], prefix)

        with patch("comparator.s3_utils.boto3.client", return_value=mock_s3):
            download_s3_dir(f"s3://bucket/{prefix}", tmp_path)

        mock_s3.download_file.assert_called_once_with(
            "bucket", key, str(tmp_path / "CANCER_REPORT" / "file.txt")
        )

    def test_empty_page_downloads_nothing(self, tmp_path):
        mock_s3 = MagicMock()
        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{}]  # no Contents key

        with patch("comparator.s3_utils.boto3.client", return_value=mock_s3):
            download_s3_dir("s3://bucket/prefix/", tmp_path)

        mock_s3.download_file.assert_not_called()


class TestUploadFile:
    def test_uploads_local_file_to_s3_uri(self, tmp_path):
        local_file = tmp_path / "result.json"
        local_file.write_text("{}")
        mock_s3 = MagicMock()

        with patch("comparator.s3_utils.boto3.client", return_value=mock_s3):
            upload_file(local_file, "s3://bucket/output/result.json")

        mock_s3.upload_file.assert_called_once_with(
            str(local_file), "bucket", "output/result.json"
        )
