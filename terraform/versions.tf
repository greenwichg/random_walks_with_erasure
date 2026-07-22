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
  backend "s3" {
    bucket       = "hidden-view-terraform-state-652615011843"
    key          = "prod/terraform.tfstate"
    region       = "us-east-1"
    profile      = "hv-terraform"
    encrypt      = true
    use_lockfile = true
  }
}
