# Group A — Elastic IP (3.86.118.17) fronting the instance, and its association.
# Modeled as allocation + separate association (the recommended split): the EIP
# resource does NOT set `instance`, so the association is owned solely by
# aws_eip_association.
resource "aws_eip" "ih_eip" {
  domain = "vpc"
}

resource "aws_eip_association" "ih_eip_assoc" {
  allocation_id = aws_eip.ih_eip.id
  instance_id   = "i-01d221c5b7b7920ed"
}
