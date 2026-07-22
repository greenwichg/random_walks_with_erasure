# Group A — edge security group (ih-beta-sg): the Wave 0 public edge (80/443).
# Lives in the default VPC (not managed). Config is generated via
# `terraform plan -generate-config-out=generated.tf`, refined to match the live
# rules exactly, and applied at plan-to-zero. Import is state-only.
import {
  to = aws_security_group.ih_beta_sg
  id = "sg-02de782b0941bc1dd"
}
