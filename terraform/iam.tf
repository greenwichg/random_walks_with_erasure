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
