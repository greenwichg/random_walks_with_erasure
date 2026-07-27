# Group A — Elastic IP (3.86.118.17) fronting the instance, and its association.
# Modeled as allocation + separate association (the recommended split): the EIP
# resource does NOT set `instance`, so the association is owned solely by
# aws_eip_association.
resource "aws_eip" "ih_eip" {
  domain = "vpc"

  # PERSISTENT TIER — the address the Route 53 A record points at. It stays ASSOCIATED across a
  # suspend (an EIP survives instance stop/start), which is why resume needs no DNS change and the
  # TLS certificate keeps working. Releasing it would force a record update plus propagation.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_eip_association" "ih_eip_assoc" {
  allocation_id = aws_eip.ih_eip.id
  instance_id   = "i-01d221c5b7b7920ed"
}
