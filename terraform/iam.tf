# Group A — IAM for the compute core.

# EC2 instance profile the production instance runs under.
resource "aws_iam_instance_profile" "ih_ec2_role" {
  name = "ih-ec2-role"
  path = "/"
  role = aws_iam_role.ih_ec2_role.name
}

import {
  to = aws_iam_instance_profile.ih_ec2_role
  id = "ih-ec2-role"
}

# IAM role the instance assumes (trusts the EC2 service).
resource "aws_iam_role" "ih_ec2_role" {
  name                 = "ih-ec2-role"
  path                 = "/"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

import {
  to = aws_iam_role.ih_ec2_role
  id = "ih-ec2-role"
}

# SSM Session Manager (shell without SSH) + CloudWatch agent (metrics/logs).
resource "aws_iam_role_policy_attachment" "ih_ec2_ssm_core" {
  role       = aws_iam_role.ih_ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

import {
  to = aws_iam_role_policy_attachment.ih_ec2_ssm_core
  id = "ih-ec2-role/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ih_ec2_cloudwatch_agent" {
  role       = aws_iam_role.ih_ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

import {
  to = aws_iam_role_policy_attachment.ih_ec2_cloudwatch_agent
  id = "ih-ec2-role/arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# Inline policy: off-host S3 backups to the Wave 0 backup bucket.
resource "aws_iam_role_policy" "ih_s3_backup" {
  name = "ih-s3-backup"
  role = aws_iam_role.ih_ec2_role.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
      Resource = [
        "arn:aws:s3:::hidden-view-ih-backups-652615011843",
        "arn:aws:s3:::hidden-view-ih-backups-652615011843/*",
      ]
    }]
  })
}

import {
  to = aws_iam_role_policy.ih_s3_backup
  id = "ih-ec2-role:ih-s3-backup"
}
