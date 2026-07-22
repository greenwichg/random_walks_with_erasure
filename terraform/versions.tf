terraform {
  # 1.10+ gives us S3-native state locking (use_lockfile) — no DynamoDB table needed.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state. The bucket is bootstrap infrastructure, created out-of-band by the
  # admin (the least-privilege exec role cannot create buckets by design) — see README.
  # Locking uses the S3-native lockfile, which lands under prod/* and is therefore
  # writable by the exec role's existing state-object permission.
  #
  # Auth is via ambient AWS_* env vars (see ./assume.sh), deliberately NOT a `profile`:
  # the S3 backend cannot prompt for MFA, so we assume the exec role with the AWS CLI
  # (which can) and export the resulting short-lived session credentials for Terraform.
  backend "s3" {
    bucket       = "hidden-view-terraform-state-652615011843"
    key          = "prod/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
