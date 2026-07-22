# Group A — IAM for the compute core.

# EC2 instance profile the production instance runs under. Brought under management
# via the import block below; `import` is state-only and changes nothing in AWS.
resource "aws_iam_instance_profile" "ih_ec2_role" {
  name = "ih-ec2-role"
  path = "/"
  role = "ih-ec2-role"
}

import {
  to = aws_iam_instance_profile.ih_ec2_role
  id = "ih-ec2-role"
}

# --- IAM role the instance assumes: SSM core + CloudWatch agent + inline S3 backup ---
# Configs are generated via `terraform plan -generate-config-out=generated.tf`, then the
# role's own `inline_policy` / `managed_policy_arns` are dropped so the attachments and
# the inline policy are managed ONLY by their dedicated resources below (avoids the
# aws_iam_role exclusive-management conflict). Applied at plan-to-zero.
import {
  to = aws_iam_role.ih_ec2_role
  id = "ih-ec2-role"
}

import {
  to = aws_iam_role_policy_attachment.ih_ec2_cloudwatch_agent
  id = "ih-ec2-role/arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

import {
  to = aws_iam_role_policy_attachment.ih_ec2_ssm_core
  id = "ih-ec2-role/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

import {
  to = aws_iam_role_policy.ih_s3_backup
  id = "ih-ec2-role:ih-s3-backup"
}
