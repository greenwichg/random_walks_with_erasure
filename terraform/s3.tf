# Group B — off-host backup bucket (hidden-view-ih-backups-652615011843).
# AWS provider v5 splits bucket config into separate resources; each is imported
# and configured to match the live settings exactly. No lifecycle, bucket policy,
# or tags exist on the live bucket, so no resources are defined for those.
resource "aws_s3_bucket" "ih_backups" {
  bucket = "hidden-view-ih-backups-652615011843"
}

import {
  to = aws_s3_bucket.ih_backups
  id = "hidden-view-ih-backups-652615011843"
}

resource "aws_s3_bucket_versioning" "ih_backups" {
  bucket = aws_s3_bucket.ih_backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

import {
  to = aws_s3_bucket_versioning.ih_backups
  id = "hidden-view-ih-backups-652615011843"
}

resource "aws_s3_bucket_public_access_block" "ih_backups" {
  bucket                  = aws_s3_bucket.ih_backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

import {
  to = aws_s3_bucket_public_access_block.ih_backups
  id = "hidden-view-ih-backups-652615011843"
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

import {
  to = aws_s3_bucket_server_side_encryption_configuration.ih_backups
  id = "hidden-view-ih-backups-652615011843"
}

resource "aws_s3_bucket_ownership_controls" "ih_backups" {
  bucket = aws_s3_bucket.ih_backups.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

import {
  to = aws_s3_bucket_ownership_controls.ih_backups
  id = "hidden-view-ih-backups-652615011843"
}
