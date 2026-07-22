# Group A — edge security group (ih-beta-sg): the Wave 0 public edge.
# In the default VPC (not managed). Rules (exclusive, inline): 80 + 443 in from
# anywhere, all egress.
resource "aws_security_group" "ih_beta_sg" {
  name        = "ih-beta-sg"
  description = "Information Health Wave0 edge (80/443 only)"
  vpc_id      = "vpc-05239174d0b1c67ee"

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
