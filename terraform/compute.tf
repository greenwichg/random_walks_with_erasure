# Group A — the production EC2 instance (Wave 0 beta box), the compute capstone.
# t3.medium in the default VPC; the 30 GB gp3 root is managed here via
# root_block_device (not a separate aws_ebs_volume).
#
# NOTE: the root volume is unencrypted (encrypted = false). That's a deliberate
# future-hardening item; the import had to match live exactly, so it's preserved
# as-is here too.
resource "aws_instance" "ih_beta" {
  ami                         = "ami-052355af2a014bd2c"
  instance_type               = "t3.medium"
  availability_zone           = "us-east-1a"
  subnet_id                   = "subnet-0cd45d252e74e5a94"
  private_ip                  = "172.31.14.176"
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.ih_ec2_role.name
  vpc_security_group_ids      = [aws_security_group.ih_beta_sg.id]

  disable_api_stop                     = false
  disable_api_termination              = false
  ebs_optimized                        = false
  hibernation                          = false
  instance_initiated_shutdown_behavior = "stop"
  monitoring                           = false
  source_dest_check                    = true
  tenancy                              = "default"

  tags = {
    Name = "ih-beta"
  }

  capacity_reservation_specification {
    capacity_reservation_preference = "open"
  }

  cpu_options {
    core_count       = 1
    threads_per_core = 2
  }

  credit_specification {
    cpu_credits = "unlimited"
  }

  enclave_options {
    enabled = false
  }

  maintenance_options {
    auto_recovery = "default"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_protocol_ipv6          = "disabled"
    http_put_response_hop_limit = 2
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  private_dns_name_options {
    enable_resource_name_dns_a_record    = false
    enable_resource_name_dns_aaaa_record = false
    hostname_type                        = "ip-name"
  }

  root_block_device {
    delete_on_termination = true
    encrypted             = false
    iops                  = 3000
    throughput            = 125
    volume_size           = 30
    volume_type           = "gp3"
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      ami,
      user_data,
    ]
  }
}
