# Every AWS operation runs as the least-privilege HiddenViewTerraformExec role,
# assumed through the MFA-gated "hv-terraform" CLI profile. No static keys here.
#
# Intentionally NO default_tags during the import phase: tags the live resources
# don't already carry would surface as changes and break the plan-to-zero import
# guarantee. Tagging, if wanted, is a deliberate later change.
provider "aws" {
  region  = "us-east-1"
  profile = "hv-terraform"
}
