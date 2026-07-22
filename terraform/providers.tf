# Every AWS operation runs as the least-privilege HiddenViewTerraformExec role.
# Auth comes from ambient AWS_* environment variables — load them with
#   source ./assume.sh
# which assumes the exec role via the MFA-gated hv-terraform profile (the CLI handles
# the MFA prompt) and exports the short-lived session credentials. We do NOT set
# `profile` here: the S3 backend can't prompt for MFA, so env-var session credentials
# are the single auth path that works for both the backend and the provider.
#
# Intentionally NO default_tags during the import phase: tags the live resources
# don't already carry would surface as changes and break the plan-to-zero import
# guarantee. Tagging, if wanted, is a deliberate later change.
provider "aws" {
  region = "us-east-1"
}
