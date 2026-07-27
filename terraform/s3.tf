# Group B — off-host backup bucket (hidden-view-ih-backups-652615011843).
# AWS provider v5 splits bucket config into separate resources. No lifecycle,
# bucket policy, or tags exist on the live bucket, so none are defined here.
resource "aws_s3_bucket" "ih_backups" {
  bucket = "hidden-view-ih-backups-652615011843"

  # PERSISTENT TIER — the off-host copy of the database, and the only thing standing between a
  # lost instance and lost user data. This guard makes a `terraform destroy` (accidental, or a
  # mis-targeted one) fail rather than delete it.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "ih_backups" {
  bucket = aws_s3_bucket.ih_backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "ih_backups" {
  bucket                  = aws_s3_bucket.ih_backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ih_backups" {
  bucket = aws_s3_bucket.ih_backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = false
  }
}

resource "aws_s3_bucket_ownership_controls" "ih_backups" {
  bucket = aws_s3_bucket.ih_backups.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Lifecycle for the off-host backup copies — the fix for unbounded S3 growth.
#
# `backup-offhost.sh` runs `aws s3 sync` WITHOUT `--delete` (deliberately: S3 keeps history the
# local disk cannot), so without a lifecycle every hourly full-copy backup ever taken is retained
# forever. Measured trajectory before this rule: ~194 GB after one month, ~25 TB and ~$574/month by
# month twelve — pure waste, since nobody restores a random hour from eight months ago.
#
# The tiers mirror how restores actually happen: recent copies stay instantly available in Standard,
# the medium tail moves to Glacier Instant Retrieval (same millisecond retrieval, ~1/5th the price),
# and everything past the horizon expires. Versioning is enabled on this bucket, so noncurrent
# versions get their own expiry — otherwise overwritten objects would accumulate invisibly.
resource "aws_s3_bucket_lifecycle_configuration" "ih_backups" {
  bucket = aws_s3_bucket.ih_backups.id

  rule {
    id     = "backups-tiering-and-expiry"
    status = "Enabled"

    filter {
      prefix = "backups/"
    }

    # Instant-retrieval tier after a week: restores stay millisecond-fast, storage drops ~80%.
    transition {
      days          = 7
      storage_class = "GLACIER_IR"
    }

    # Long-tail archive after a quarter.
    transition {
      days          = 90
      storage_class = "DEEP_ARCHIVE"
    }

    # Horizon. 365 days is far beyond any realistic restore window for this product, and well past
    # the local tiered policy (23 files, ~4 weeks) that handles everyday recovery.
    expiration {
      days = 365
    }

    # Versioning is on: an overwritten object leaves a noncurrent version that is otherwise billed
    # forever. Backup filenames are timestamped and unique, so this should rarely fire — it exists
    # so the invisible case cannot accumulate.
    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    # Never keep paying for a failed multipart upload.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
