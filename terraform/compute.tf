# Group A — the production EC2 instance (Wave 0 beta box), the compute capstone.
# t3.medium in the default VPC; root is the 30 GB gp3 volume (managed here via
# root_block_device, NOT as a separate aws_ebs_volume). Config is generated via
# `terraform plan -generate-config-out=generated.tf`, then reconciled to plan-to-zero
# (references wired to the already-imported SG and instance profile). Import is
# state-only — it changes nothing on the running box.
import {
  to = aws_instance.ih_beta
  id = "i-01d221c5b7b7920ed"
}
