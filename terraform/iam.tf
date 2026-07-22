# Group A — IAM for the compute core.
#
# Pilot import: the EC2 instance profile the production instance runs under.
# The matching resource config is generated with
#   terraform plan -generate-config-out=generated.tf
# then reviewed and moved here, and the import is applied only once the plan is
# 0 to add / 0 to change / 0 to destroy. `import` is state-only — it changes
# nothing in AWS; if the plan ever shows a change or destroy, we stop and fix config.
import {
  to = aws_iam_instance_profile.ih_ec2_role
  id = "ih-ec2-role"
}
