# Group A — Elastic IP (3.86.118.17) fronting the instance, and its association.
# Modeled as allocation + separate association (the recommended split): the EIP
# resource does NOT set `instance`, so the association is owned solely by
# aws_eip_association. instance_id is the live instance (imported next).
resource "aws_eip" "ih_eip" {
  domain = "vpc"
}

import {
  to = aws_eip.ih_eip
  id = "eipalloc-0ff44d5be53b01ae9"
}

resource "aws_eip_association" "ih_eip_assoc" {
  allocation_id = aws_eip.ih_eip.id
  instance_id   = "i-01d221c5b7b7920ed"
}

import {
  to = aws_eip_association.ih_eip_assoc
  id = "eipassoc-0367e06930213a50c"
}
